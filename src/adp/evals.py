"""Evaluation harness: ground-truth scoring of pipeline runs, metrics, and regression checks.

The pipeline never sees the hidden tests. After a run, the final code is scored against them, which lets us measure
how good the *quality gate* is (precision, false accepts, false rejects), not just how good the model is.
"""
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import DeliveryResult
from .sandbox import run_pytest


@dataclass
class Task:
    id: str
    title: str
    requirement: str
    reference: str
    hidden_tests: str


def default_tasks_dir() -> Path:
    return Path(os.environ.get("ADP_TASKS_DIR") or Path(__file__).resolve().parents[2] / "tasks")


def load_tasks(tasks_dir: Optional[Path] = None, ids: Optional[List[str]] = None) -> List[Task]:
    d = Path(tasks_dir) if tasks_dir else default_tasks_dir()
    out: List[Task] = []
    for p in sorted(d.iterdir()):
        if not (p / "task.json").exists():
            continue
        meta = json.loads((p / "task.json").read_text())
        if ids and meta["id"] not in ids:
            continue
        out.append(Task(meta["id"], meta["title"], meta["requirement"], (p / "reference.py").read_text(),
                        (p / "hidden_tests.py").read_text()))
    if ids:
        missing = set(ids) - {t.id for t in out}
        if missing:
            raise ValueError(f"unknown task id(s): {sorted(missing)}")
    return out


def score_hidden(task: Task, code: str, timeout_s: int = 20) -> bool:
    if not code.strip():
        return False
    return run_pytest({"solution.py": code, "test_hidden.py": task.hidden_tests}, timeout_s=timeout_s).passed


def wilson(k: int, n: int, z: float = 1.96) -> List[float]:
    if n == 0:
        return [0.0, 0.0]
    p, den = k / n, 1 + z * z / n
    c, h = p + z * z / (2 * n), z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return [round(max(0.0, (c - h) / den), 3), round(min(1.0, (c + h) / den), 3)]


def task_row(r: DeliveryResult, hidden_passed: bool) -> Dict[str, Any]:
    accepted = bool(r.gate and r.gate.accepted)
    return {
        "task_id": r.task_id, "status": r.status, "gate_accepted": accepted, "hidden_passed": hidden_passed,
        "iterations": r.iterations, "guardrail_violations": len(r.guardrail_violations),
        "review_score": r.review.score if r.review else None,
        "gate_reasons": (r.gate.reasons if r.gate else []), "error": r.error, "trace_id": r.trace_id,
        "llm_calls": r.usage.llm_calls, "tokens": r.usage.prompt_tokens + r.usage.completion_tokens,
        "llm_latency_ms": r.usage.latency_ms,
    }


def compute_metrics(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(rows)
    passed = sum(r["hidden_passed"] for r in rows)
    accepted = [r for r in rows if r["gate_accepted"]]
    rejected = [r for r in rows if not r["gate_accepted"]]
    false_accept = sum(1 for r in accepted if not r["hidden_passed"])
    false_reject = sum(1 for r in rejected if r["hidden_passed"])
    statuses: Dict[str, int] = {}
    for r in rows:
        statuses[r["status"]] = statuses.get(r["status"], 0) + 1
    return {
        "n_tasks": n,
        "hidden_pass": passed, "hidden_pass_rate": round(passed / n, 3) if n else 0.0, "hidden_pass_ci95": wilson(passed, n),
        "gate_accepted": len(accepted), "gate_accept_rate": round(len(accepted) / n, 3) if n else 0.0,
        "gate_precision": round((len(accepted) - false_accept) / len(accepted), 3) if accepted else None,
        "false_accepts": false_accept, "false_rejects": false_reject,
        "guardrail_blocked": sum(1 for r in rows if r["guardrail_violations"]),
        "avg_repair_iterations": round(sum(r["iterations"] for r in rows) / n, 2) if n else 0.0,
        "total_llm_calls": sum(r["llm_calls"] for r in rows), "total_tokens": sum(r["tokens"] for r in rows),
        "avg_llm_latency_s": round(sum(r["llm_latency_ms"] for r in rows) / n / 1000, 1) if n else 0.0,
        "statuses": statuses,
    }


def compare(current: Dict[str, Any], baseline: Dict[str, Any], hidden_pass_drop: float = 0.0,
            allow_new_false_accepts: int = 0) -> List[str]:
    """Return human-readable regressions of `current` against `baseline` for each variant present in both."""
    out: List[str] = []
    for name, cur in current.get("variants", {}).items():
        base = baseline.get("variants", {}).get(name)
        if not base:
            continue
        cm, bm = cur["metrics"], base["metrics"]
        if cm["hidden_pass_rate"] < bm["hidden_pass_rate"] - hidden_pass_drop - 1e-9:
            out.append(f"{name}: hidden pass rate fell {bm['hidden_pass_rate']:.1%} -> {cm['hidden_pass_rate']:.1%}")
        if cm["false_accepts"] > bm["false_accepts"] + allow_new_false_accepts:
            out.append(f"{name}: false accepts rose {bm['false_accepts']} -> {cm['false_accepts']} (gate let bad work through)")
        bt = {t["task_id"]: t for t in base["tasks"]}
        for t in cur["tasks"]:
            if t["task_id"] in bt and bt[t["task_id"]]["hidden_passed"] and not t["hidden_passed"]:
                out.append(f"{name}/{t['task_id']}: passed in baseline, fails now")
    return out
