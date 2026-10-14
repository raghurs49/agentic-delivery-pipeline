import asyncio
import json

import pytest
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import CancelledError

from adp.llm import LLMError
from adp.models import ApprovalDecision, Spec
from adp.workflows import DeliveryWorkflow, EvalBatchWorkflow
from tests.helpers import BUGGY, GOOD, SPEC_JSON, block, make_responder, run_scenario

pytestmark = pytest.mark.integration


async def start(h, **kw):
    return await h.client.start_workflow(DeliveryWorkflow.run, h.inp(**kw), id=h.wf_id(), task_queue=h.queue)


def test_happy_path_delivers_and_writes_artifacts(temporal_address, tmp_path):
    async def sc(h):
        res = await (await start(h)).result()
        return h, res
    h, res = run_scenario(temporal_address, tmp_path, make_responder(), sc)
    assert res.status == "delivered" and res.gate.accepted
    assert res.iterations == 0 and res.test_report.passed and res.review.score == 5
    assert res.usage.llm_calls == 4                       # spec + tests + implement + review
    assert res.trace_id and len(res.trace_id) == 32
    d = tmp_path / "runs" / "add"
    run_dir = next(d.iterdir())
    assert (run_dir / "solution.py").read_text().strip().endswith("return a + b")
    assert json.loads((run_dir / "result.json").read_text())["status"] == "delivered"
    assert "delivered" in (run_dir / "report.md").read_text()


def test_human_approval_by_signal(temporal_address, tmp_path):
    async def sc(h):
        handle = await start(h, auto_approve=False)
        for _ in range(100):
            st = await handle.query(DeliveryWorkflow.status)
            if st.state == "awaiting_approval":
                break
            await asyncio.sleep(0.1)
        assert st.state == "awaiting_approval" and st.spec and st.spec.signature.startswith("def add")
        calls_before = len(h.provider.calls)
        await asyncio.sleep(0.5)
        assert len(h.provider.calls) == calls_before     # nothing runs while waiting for the human
        await handle.signal(DeliveryWorkflow.approve_spec, ApprovalDecision(approved=True, reviewer="raghu"))
        return await handle.result()
    res = run_scenario(temporal_address, tmp_path, make_responder(), sc)
    assert res.status == "delivered"


def test_edited_spec_is_used(temporal_address, tmp_path):
    seen = {}

    def impl(n):
        return block(GOOD)

    async def sc(h):
        handle = await start(h, auto_approve=False)
        for _ in range(100):
            if (await handle.query(DeliveryWorkflow.status)).state == "awaiting_approval":
                break
            await asyncio.sleep(0.1)
        edited = Spec(summary="EDITED", signature="def add(a: int, b: int) -> int",
                      acceptance_criteria=["EDITED CRITERION"], edge_cases=[])
        await handle.signal(DeliveryWorkflow.approve_spec, ApprovalDecision(approved=True, edited_spec=edited))
        res = await handle.result()
        seen["tests_prompt"] = next(c["user"] for c in h.provider.calls if c["stage"] == "tests")
        return res
    res = run_scenario(temporal_address, tmp_path, make_responder(), sc)
    assert res.spec.summary == "EDITED" and "EDITED CRITERION" in seen["tests_prompt"]


def test_rejection_by_reviewer_stops_before_any_code(temporal_address, tmp_path):
    async def sc(h):
        handle = await start(h, auto_approve=False)
        for _ in range(100):
            if (await handle.query(DeliveryWorkflow.status)).state == "awaiting_approval":
                break
            await asyncio.sleep(0.1)
        await handle.signal(DeliveryWorkflow.approve_spec, ApprovalDecision(approved=False, comment="spec is wrong"))
        return h, await handle.result()
    h, res = run_scenario(temporal_address, tmp_path, make_responder(), sc)
    assert res.status == "rejected_by_reviewer" and res.error == "spec is wrong"
    assert [c["stage"] for c in h.provider.calls] == ["spec"]


def test_approval_timeout(temporal_address, tmp_path):
    async def sc(h):
        return await (await start(h, auto_approve=False, approval_timeout_s=1)).result()
    res = run_scenario(temporal_address, tmp_path, make_responder(), sc)
    assert res.status == "approval_timeout"


def test_repair_loop_fixes_a_buggy_first_attempt(temporal_address, tmp_path):
    resp = make_responder({"implement": lambda n: block(BUGGY if n == 0 else GOOD)})

    async def sc(h):
        return h, await (await start(h)).result()
    h, res = run_scenario(temporal_address, tmp_path, resp, sc)
    assert res.status == "delivered" and res.iterations == 1
    repair_prompt = [c["user"] for c in h.provider.calls if c["stage"].startswith("implement")][1]
    assert "failed these tests" in repair_prompt and "return a - b" in repair_prompt


def test_naive_variant_has_no_repair_and_is_rejected_by_the_gate(temporal_address, tmp_path):
    resp = make_responder({"implement": lambda n: block(BUGGY)})

    async def sc(h):
        return await (await start(h, variant="v1-naive")).result()
    res = run_scenario(temporal_address, tmp_path, resp, sc)
    assert res.status == "rejected_by_gate" and res.iterations == 0
    assert "tests failed" in res.gate.reasons[0]


def test_repairs_are_bounded(temporal_address, tmp_path):
    resp = make_responder({"implement": lambda n: block(BUGGY)})

    async def sc(h):
        return h, await (await start(h)).result()
    h, res = run_scenario(temporal_address, tmp_path, resp, sc)
    assert res.status == "rejected_by_gate" and res.iterations == 2
    assert sum(1 for c in h.provider.calls if c["stage"].startswith("implement")) == 3


def test_guardrail_blocks_code_even_when_tests_pass(temporal_address, tmp_path):
    evil = "import os\n\ndef add(a, b):\n    os.system('echo hi')\n    return a + b\n"
    resp = make_responder({"implement": lambda n: block(evil)})

    async def sc(h):
        return await (await start(h)).result()
    res = run_scenario(temporal_address, tmp_path, resp, sc)
    assert res.status == "rejected_by_gate"
    assert any("forbidden import: os" in v for v in res.guardrail_violations)
    assert "guardrail" in res.gate.reasons[0]


def test_vacuous_generated_tests_are_caught(temporal_address, tmp_path):
    weak = "from solution import add\n\ndef test_a():\n    assert True\n\ndef test_b():\n    assert True\n"
    resp = make_responder({"tests": lambda n: block(weak)})

    async def sc(h):
        return await (await start(h)).result()
    res = run_scenario(temporal_address, tmp_path, resp, sc)
    assert res.status == "rejected_by_gate"
    assert any(v.startswith("tests:") for v in res.guardrail_violations)


def test_invalid_model_output_is_not_retried_by_temporal(temporal_address, tmp_path):
    resp = make_responder({"spec": lambda n: "I cannot comply."})

    async def sc(h):
        return h, await (await start(h)).result()
    h, res = run_scenario(temporal_address, tmp_path, resp, sc)
    assert res.status == "failed" and "InvalidModelOutput" in res.error
    assert sum(1 for c in h.provider.calls if c["stage"].startswith("spec")) == 3   # 3 re-prompts, zero Temporal retries


def test_transient_llm_error_is_retried_with_backoff(temporal_address, tmp_path):
    state = {"failed": False}

    def impl(n):
        if not state["failed"]:
            state["failed"] = True
            raise LLMError("ollama unreachable")
        return block(GOOD)
    resp = make_responder({"implement": impl})

    async def sc(h):
        return await (await start(h)).result()
    res = run_scenario(temporal_address, tmp_path, resp, sc)
    assert res.status == "delivered" and state["failed"]


def test_reviewer_that_cannot_answer_rejects_instead_of_crashing(temporal_address, tmp_path):
    resp = make_responder({"review": lambda n: "lgtm!"})

    async def sc(h):
        return await (await start(h)).result()
    res = run_scenario(temporal_address, tmp_path, resp, sc)
    assert res.status == "rejected_by_gate" and res.review.score == 0


def test_cancellation_while_waiting_leaves_an_audit_trail(temporal_address, tmp_path):
    async def sc(h):
        handle = await start(h, auto_approve=False)
        for _ in range(100):
            if (await handle.query(DeliveryWorkflow.status)).state == "awaiting_approval":
                break
            await asyncio.sleep(0.1)
        await handle.cancel()
        with pytest.raises(WorkflowFailureError) as ei:
            await handle.result()
        assert isinstance(ei.value.cause, CancelledError)
    run_scenario(temporal_address, tmp_path, make_responder(), sc)
    run_dir = next((tmp_path / "runs" / "add").iterdir())
    assert json.loads((run_dir / "result.json").read_text())["status"] == "cancelled"


def test_eval_batch_runs_children_with_bounded_concurrency(temporal_address, tmp_path):
    async def sc(h):
        inputs = [h.inp(task_id=f"t{i}") for i in range(3)]
        return await h.client.execute_workflow(EvalBatchWorkflow.run, args=[inputs, 2], id=h.wf_id(), task_queue=h.queue)
    results = run_scenario(temporal_address, tmp_path, make_responder(), sc)
    assert [r.task_id for r in results] == ["t0", "t1", "t2"]
    assert all(r.status == "delivered" for r in results)


def test_retry_feeds_the_specific_error_back_to_the_model(temporal_address, tmp_path):
    """Temperature-0 models repeat themselves, so a retry must change the prompt."""
    resp = make_responder({"spec": lambda n: "{not json" if n == 0 else SPEC_JSON})

    async def sc(h):
        return h, await (await start(h)).result()
    h, res = run_scenario(temporal_address, tmp_path, resp, sc)
    assert res.status == "delivered"
    spec_calls = [c for c in h.provider.calls if c["stage"].startswith("spec")]
    assert [c["stage"] for c in spec_calls] == ["spec", "spec#1"]
    assert "could not be used because" in spec_calls[1]["user"] and "valid JSON" in spec_calls[1]["user"]
    assert "could not be used" not in spec_calls[0]["user"]


def test_poor_generated_tests_are_regenerated_with_feedback(temporal_address, tmp_path):
    from tests.helpers import TESTS
    weak = "def test_a():\n    assert 1 == 1\n"
    resp = make_responder({"tests": lambda n: block(weak if n == 0 else TESTS)})

    async def sc(h):
        return h, await (await start(h)).result()
    h, res = run_scenario(temporal_address, tmp_path, resp, sc)
    assert res.status == "delivered" and res.guardrail_violations == []
    calls = [c for c in h.provider.calls if c["stage"].startswith("tests")]
    assert len(calls) == 2 and "rejected by the quality check" in calls[1]["user"]
    assert "from solution import add" in calls[0]["user"]          # the exact symbol is named up front


def test_tests_that_stay_poor_after_regeneration_reject_the_run(temporal_address, tmp_path):
    weak = "def test_a():\n    assert 1 == 1\n"
    resp = make_responder({"tests": lambda n: block(weak)})

    async def sc(h):
        return h, await (await start(h)).result()
    h, res = run_scenario(temporal_address, tmp_path, resp, sc)
    assert res.status == "rejected_by_gate" and any(v.startswith("tests:") for v in res.guardrail_violations)
    assert sum(1 for c in h.provider.calls if c["stage"].startswith("tests")) == 3   # bounded
