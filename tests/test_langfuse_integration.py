"""Live check that a run produces one Langfuse trace with spans, generations (with token usage) and scores.

Skipped unless LANGFUSE_HOST / LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY point at a reachable Langfuse.
"""
import os
import time

import pytest

from adp.config import Settings
from adp.observability import LangfuseTracer, trace_id_for

pytestmark = pytest.mark.integration

HOST = os.environ.get("LANGFUSE_HOST", "")
PK, SK = os.environ.get("LANGFUSE_PUBLIC_KEY", ""), os.environ.get("LANGFUSE_SECRET_KEY", "")


@pytest.fixture(scope="module")
def langfuse_ready():
    if not (HOST and PK and SK):
        pytest.skip("Langfuse env not configured")
    try:
        from langfuse import Langfuse
        client = Langfuse(public_key=PK, secret_key=SK, host=HOST)
        if not client.auth_check():
            pytest.skip("Langfuse rejected the credentials")
    except Exception:
        pytest.skip("Langfuse not reachable")
    return client


def test_run_is_traced_with_generations_and_scores(temporal_address, tmp_path, langfuse_ready):
    import asyncio
    import uuid

    from adp.activities import DeliveryActivities
    from adp.workflows import DeliveryWorkflow
    from tests.helpers import make_responder
    from concurrent.futures import ThreadPoolExecutor

    from temporalio.client import Client
    from temporalio.worker import Worker

    from adp.llm import FakeProvider
    from adp.workflows import EvalBatchWorkflow
    from tests.helpers import REQ

    wf_id = f"lf-it-{uuid.uuid4().hex[:8]}"
    queue = f"lf-{uuid.uuid4().hex[:6]}"
    settings = Settings(output_dir=str(tmp_path / "runs"), task_queue=queue)
    tracer = LangfuseTracer(HOST, PK, SK)
    provider = FakeProvider(make_responder())

    async def main():
        client = await Client.connect(temporal_address)
        acts = DeliveryActivities(provider, tracer, settings)
        with ThreadPoolExecutor(max_workers=4) as pool:
            async with Worker(client, task_queue=queue, workflows=[DeliveryWorkflow, EvalBatchWorkflow],
                              activities=acts.all(), activity_executor=pool):
                from adp.models import DeliveryInput
                return await client.execute_workflow(
                    DeliveryWorkflow.run, DeliveryInput(task_id="add", requirement=REQ, auto_approve=True),
                    id=wf_id, task_queue=queue)
    res = asyncio.run(main())
    tracer.flush()
    assert res.status == "delivered"
    tid = trace_id_for(wf_id)
    assert res.trace_id == tid

    client = langfuse_ready
    obs, scores, last_err = [], {}, None
    for _ in range(40):                      # ingestion is asynchronous (worker + ClickHouse)
        try:
            obs = client.api.observations.get_many(trace_id=tid, fields="core,basic,io,model,usage", limit=100).data
            scores = {s.name: s.value for s in client.api.scores_v3.get_many_v3(trace_id=tid, limit=100).data}
            if len(obs) >= 9 and {"gate_accepted", "tests_pass_ratio", "review_score", "guardrail_violations", "repair_iterations"} <= set(scores):
                break
        except Exception as e:               # keep the last error so a real API problem is not hidden by the retry loop
            last_err = e
        time.sleep(1.5)
    assert obs, f"trace never appeared in Langfuse (last error: {last_err!r})"
    assert scores, f"no scores arrived (last error: {last_err!r})"
    names = {o.name for o in obs}
    assert {"generate_spec", "generate_tests", "implement#0", "run_tests", "review", "finalize"} <= names
    gens = [o for o in obs if o.type == "GENERATION"]
    assert len(gens) == 4 and all(g.model for g in gens)
    assert all((g.usage_details or {}).get("input", 0) > 0 for g in gens), "token usage missing on generations"
    assert all(o.session_id == wf_id for o in obs), "session id (= workflow id) must be on every observation"
    assert scores["gate_accepted"] == 1 and scores["review_score"] == 5 and scores["tests_pass_ratio"] == 1.0
