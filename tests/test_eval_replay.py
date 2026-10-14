"""Deterministic regression test: replay the recorded model generations through the real pipeline and require the
same outcomes as the committed evaluation results.

This runs in CI without any model. It fails if a change to the gate, guardrails, parsing, repair loop or sandbox alters
what the pipeline does with the same model outputs. If you change a PROMPT, the recording no longer matches (the replay
provider raises "no recording"): re-record with scripts/run_evals.sh and review the new results before committing them.
"""
import asyncio
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from temporalio.client import Client
from temporalio.worker import Worker

from adp.activities import DeliveryActivities
from adp.config import Settings
from adp.eval_runner import run_eval
from adp.evals import compare, load_tasks
from adp.llm import ReplayProvider
from adp.observability import NullTracer
from adp.workflows import DeliveryWorkflow, EvalBatchWorkflow

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[1]
RUNS = sorted(p.stem for p in (ROOT / "evals" / "results").glob("*.json") if (ROOT / "evals" / "recordings" / f"{p.stem}.json").exists())


def _key_view(result):
    return {v: {t["task_id"]: (t["status"], t["gate_accepted"], t["hidden_passed"]) for t in d["tasks"]}
            for v, d in result["variants"].items()}


@pytest.mark.parametrize("name", RUNS or [pytest.param("none", marks=pytest.mark.skip("no committed evaluation runs"))])
def test_replay_reproduces_committed_results(temporal_address, tmp_path, name):
    baseline = json.loads((ROOT / "evals" / "results" / f"{name}.json").read_text())
    meta = baseline["meta"]
    queue = f"replay-{uuid.uuid4().hex[:8]}"
    settings = Settings(provider="replay", recordings_path=str(ROOT / "evals" / "recordings" / f"{name}.json"),
                        spec_model=meta["spec_model"], code_model=meta["code_model"], review_model=meta["review_model"],
                        output_dir=str(tmp_path / "runs"), task_queue=queue)
    variants = list(baseline["variants"])
    tasks = load_tasks()

    async def main():
        client = await Client.connect(temporal_address)
        acts = DeliveryActivities(ReplayProvider(settings.recordings_path), NullTracer(), settings)
        with ThreadPoolExecutor(max_workers=4) as pool:
            async with Worker(client, task_queue=queue, workflows=[DeliveryWorkflow, EvalBatchWorkflow],
                              activities=acts.all(), activity_executor=pool, max_concurrent_activities=4):
                return await run_eval(client, settings, NullTracer(), variants, tasks, concurrency=4, log=lambda m: None)
    current = asyncio.run(main())

    assert compare(current, baseline) == []                       # no regression in pass rate, false accepts, per task
    assert _key_view(current) == _key_view(baseline)              # and identical outcomes task by task
