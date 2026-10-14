"""LLM providers behind one small interface.

* OllamaProvider: free, local models (default).
* ReplayProvider: replays recorded generations by prompt hash, so CI can run the whole pipeline deterministically.
* RecordingProvider: wraps another provider and records every generation to a JSON file.
* FakeProvider: scripted responses for unit tests.
"""
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Dict, Optional


@dataclass
class LLMResult:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0


class LLMError(RuntimeError):
    """Transient failure (connection refused, timeout, 5xx). Safe to retry."""


class LLMProvider:
    def generate(self, *, stage: str, model: str, system: str, user: str, temperature: float = 0.0,
                 seed: int = 7, max_tokens: int = 1200) -> LLMResult:  # pragma: no cover - interface
        raise NotImplementedError


def prompt_key(stage: str, model: str, system: str, user: str) -> str:
    return hashlib.sha256("\x1f".join([stage, model, system, user]).encode("utf-8")).hexdigest()


class OllamaProvider(LLMProvider):
    def __init__(self, base_url: str = "http://localhost:11434", timeout_s: int = 180):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def generate(self, *, stage, model, system, user, temperature=0.0, seed=7, max_tokens=1200) -> LLMResult:
        body = json.dumps({
            "model": model, "stream": False,
            "options": {"temperature": temperature, "seed": seed, "num_predict": max_tokens},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }).encode("utf-8")
        req = urllib.request.Request(self.base_url + "/api/chat", body, {"Content-Type": "application/json"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                data = json.load(r)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise LLMError(f"ollama unreachable or timed out: {e}") from e
        text = (data.get("message") or {}).get("content", "")
        return LLMResult(text=text, model=model, prompt_tokens=int(data.get("prompt_eval_count") or 0),
                         completion_tokens=int(data.get("eval_count") or 0), latency_ms=int((time.time() - t0) * 1000))


class RecordingProvider(LLMProvider):
    """Records every generation (prompt, output, usage) to a JSON file keyed by prompt hash.

    Safe against several processes recording to the same file: each write takes an exclusive file lock, re-reads the
    file, merges the new entry and replaces the file atomically (an unlocked read-modify-write loses entries).
    """

    def __init__(self, inner: LLMProvider, path: str):
        self.inner, self.path = inner, path

    def _record(self, key: str, entry: dict) -> None:
        import fcntl
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path + ".lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            data: Dict[str, dict] = {}
            if os.path.exists(self.path):
                with open(self.path) as f:
                    data = json.load(f)
            data[key] = entry
            tmp = f"{self.path}.{os.getpid()}.tmp"
            with open(tmp, "w") as f:
                json.dump(data, f, indent=1, sort_keys=True)
            os.replace(tmp, self.path)

    def generate(self, *, stage, model, system, user, **kw) -> LLMResult:
        res = self.inner.generate(stage=stage, model=model, system=system, user=user, **kw)
        self._record(prompt_key(stage, model, system, user), {
            "stage": stage, "model": model, "system": system, "user": user,      # kept so a replay mismatch can be diffed
            "text": res.text, "prompt_tokens": res.prompt_tokens, "completion_tokens": res.completion_tokens,
            "latency_ms": res.latency_ms})
        return res


class ReplayProvider(LLMProvider):
    def __init__(self, path: str):
        with open(path) as f:
            self._data: Dict[str, dict] = json.load(f)

    def generate(self, *, stage, model, system, user, **kw) -> LLMResult:
        rec = self._data.get(prompt_key(stage, model, system, user))
        if rec is None:
            raise KeyError(f"no recording for stage={stage} model={model} (prompt changed? re-record with ADP_RECORD_TO)")
        return LLMResult(text=rec["text"], model=model, prompt_tokens=rec.get("prompt_tokens", 0),
                         completion_tokens=rec.get("completion_tokens", 0), latency_ms=rec.get("latency_ms", 0))


class FakeProvider(LLMProvider):
    """Scripted provider for tests: `responder(stage, system, user, call_index)` returns the text."""

    def __init__(self, responder: Callable[[str, str, str, int], str]):
        self.responder = responder
        self.calls = []
        self._lock = threading.Lock()

    def generate(self, *, stage, model, system, user, **kw) -> LLMResult:
        with self._lock:
            idx = len(self.calls)
            self.calls.append({"stage": stage, "model": model, "user": user})
        text = self.responder(stage, system, user, idx)
        return LLMResult(text=text, model=model, prompt_tokens=len((system + user).split()),
                         completion_tokens=len(text.split()), latency_ms=1)


def build_provider(settings) -> LLMProvider:
    if settings.provider == "replay":
        return ReplayProvider(settings.recordings_path)
    base: LLMProvider = OllamaProvider(settings.ollama_url, settings.llm_timeout_s)
    if settings.record_to:
        return RecordingProvider(base, settings.record_to)
    return base
