"""Run generated tests against generated code in a throwaway directory with a hard timeout.

Best-effort isolation only: temp dir, scrubbed environment, CPU/file-size rlimits, killed process group.
It is NOT a security boundary. For untrusted code in production use containers/gVisor and no network.
"""
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from typing import Dict

from .models import TestReport

_SUMMARY = re.compile(r"(\d+) (passed|failed|error|errors)")


def scrub(text: str, tmpdir: str) -> str:
    """Make pytest output deterministic: no temp paths, timings or memory addresses.

    The output is fed back to the model in repair prompts, so anything that changes between identical runs would make
    runs unrepeatable (and break recorded-generation replay) and would only add noise for the model.
    """
    # Longest first and in a fixed order: on macOS /var/x and /private/var/x are the same folder, and replacing the
    # shorter one first would leave "/private<sandbox>" (set iteration order varies between processes).
    for d in sorted({tmpdir, os.path.realpath(tmpdir)}, key=lambda x: (-len(x), x)):
        text = text.replace(d, "<sandbox>")
    text = re.sub(r"\bin \d+(?:\.\d+)?s\b", "in <t>s", text)
    return re.sub(r"0x[0-9a-fA-F]+", "0x...", text)


def _limits(cpu_s: int):
    def apply():  # runs in the child before exec (POSIX only)
        try:
            import resource
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s + 1))
            resource.setrlimit(resource.RLIMIT_FSIZE, (5_000_000, 5_000_000))
        except Exception:
            pass
        os.setsid()
    return apply


def run_pytest(files: Dict[str, str], timeout_s: int = 20, target: str = "") -> TestReport:
    """files: relative filename -> content. Runs `pytest` in a temp dir; `target` limits to one test file."""
    with tempfile.TemporaryDirectory(prefix="adp-sandbox-") as d:
        for name, content in files.items():
            if os.path.isabs(name) or ".." in name.split(os.sep):
                return TestReport(passed=False, output=f"refused unsafe filename: {name}")
            path = os.path.join(d, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(content)
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1",
               "HOME": d, "LANG": "C.UTF-8"}
        cmd = [sys.executable, "-I", "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=short", "-W", "ignore"]
        if target:
            cmd.append(target)
        t0 = time.time()
        proc = subprocess.Popen(cmd, cwd=d, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                preexec_fn=_limits(timeout_s) if os.name == "posix" else None)
        timed_out = False
        try:
            out, _ = proc.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except Exception:
                proc.kill()
            out, _ = proc.communicate()
        dur = round(time.time() - t0, 2)
        counts = {"passed": 0, "failed": 0, "error": 0}
        for n, kind in _SUMMARY.findall(out or ""):
            counts["error" if kind.startswith("error") else kind] += int(n)
        n_failed = counts["failed"] + counts["error"]
        ok = (proc.returncode == 0) and counts["passed"] > 0 and n_failed == 0 and not timed_out
        text = scrub(out or "", d)[-1800:]
        return TestReport(passed=ok, n_passed=counts["passed"], n_failed=n_failed, timed_out=timed_out,
                          output=text, duration_s=dur)
