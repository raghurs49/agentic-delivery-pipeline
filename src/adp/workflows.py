"""Temporal workflows.

DeliveryWorkflow: requirement -> spec -> human approval (signal, with timeout) -> tests (written from the spec, BEFORE
the implementation, so the agent cannot tailor them to its own code) -> implement/repair loop -> review -> quality gate
-> artifacts. Workflow code is deterministic; all I/O happens in activities.

EvalBatchWorkflow: runs many DeliveryWorkflows as children with bounded concurrency (used by the evaluation harness).
"""
import asyncio
from datetime import timedelta
from typing import List, Optional

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from .activities import DeliveryActivities
    from .agents import get_variant
    from .gate import evaluate_gate
    from .models import (ApprovalDecision, DeliveryInput, DeliveryResult, GateDecision, ImplResult, ReviewResult,
                         SpecResult, StatusView, TestReport, TestsResult, Usage)
    from .observability import trace_id_for

LLM_RETRY = RetryPolicy(initial_interval=timedelta(seconds=2), backoff_coefficient=2.0,
                        maximum_interval=timedelta(seconds=30), maximum_attempts=3,
                        non_retryable_error_types=["InvalidModelOutput"])
LLM_OPTS = dict(start_to_close_timeout=timedelta(minutes=6), heartbeat_timeout=timedelta(seconds=60), retry_policy=LLM_RETRY)
LOCAL_OPTS = dict(start_to_close_timeout=timedelta(minutes=2),
                  retry_policy=RetryPolicy(initial_interval=timedelta(seconds=1), maximum_attempts=3))


def _add(total: Usage, u: Usage) -> None:
    total.llm_calls += u.llm_calls
    total.prompt_tokens += u.prompt_tokens
    total.completion_tokens += u.completion_tokens
    total.latency_ms += u.latency_ms


@workflow.defn
class DeliveryWorkflow:
    def __init__(self) -> None:
        self._approval: Optional[ApprovalDecision] = None
        self._state = "starting"
        self._spec = None
        self._iteration = 0
        self._detail = ""

    @workflow.signal
    def approve_spec(self, decision: ApprovalDecision) -> None:
        """Human checkpoint: approve, reject, or approve with an edited spec."""
        if self._approval is None:
            self._approval = decision

    @workflow.query
    def status(self) -> StatusView:
        return StatusView(state=self._state, spec=self._spec, iteration=self._iteration, detail=self._detail)

    @workflow.run
    async def run(self, inp: DeliveryInput) -> DeliveryResult:
        wf_id = workflow.info().workflow_id
        trace_id = trace_id_for(wf_id)
        v = get_variant(inp.variant)
        usage = Usage()
        result = DeliveryResult(task_id=inp.task_id, variant=inp.variant, status="failed", trace_id=trace_id, usage=usage)
        try:
            # 1. specification
            self._state = "specifying"
            sr: SpecResult = await workflow.execute_activity_method(
                DeliveryActivities.generate_spec, args=[inp, trace_id], **LLM_OPTS)
            _add(usage, sr.usage)
            spec = result.spec = self._spec = sr.spec

            # 2. human checkpoint
            if not inp.auto_approve:
                self._state = "awaiting_approval"
                try:
                    await workflow.wait_condition(lambda: self._approval is not None,
                                                  timeout=timedelta(seconds=inp.approval_timeout_s))
                except asyncio.TimeoutError:
                    result.status, result.error = "approval_timeout", "no decision within the approval window"
                    return await self._finish(result, wf_id)
                if not self._approval.approved:
                    result.status, result.error = "rejected_by_reviewer", self._approval.comment
                    return await self._finish(result, wf_id)
                if self._approval.edited_spec is not None:
                    spec = result.spec = self._spec = self._approval.edited_spec

            # 3. tests from the spec, written before any code exists
            self._state = "writing_tests"
            tr: TestsResult = await workflow.execute_activity_method(
                DeliveryActivities.generate_tests, args=[inp, spec, trace_id], **LLM_OPTS)
            _add(usage, tr.usage)
            tests = tr.tests
            result.tests_code = tests.code

            # 4. implement, run tests, repair a bounded number of times
            code, failures, report, impl_violations = "", "", None, []
            for it in range(v.max_repairs + 1):
                self._state, self._iteration = "implementing", it
                ir: ImplResult = await workflow.execute_activity_method(
                    DeliveryActivities.implement, args=[inp, spec, code, failures, it, trace_id], **LLM_OPTS)
                _add(usage, ir.usage)
                code, impl_violations = ir.impl.code, ir.impl.violations
                self._state = "testing"
                report = await workflow.execute_activity_method(
                    DeliveryActivities.run_tests, args=[inp, code, tests.code, trace_id], **LOCAL_OPTS)
                result.iterations = it
                if report.passed and not impl_violations:
                    break
                failures = (report.output or "")[-1200:]
                if impl_violations:
                    failures += "\nGuardrails: " + "; ".join(impl_violations)
            result.code, result.test_report = code, report
            result.guardrail_violations = list(impl_violations) + list(tests.violations)

            # 5. review and gate
            self._state = "reviewing"
            rr: ReviewResult = await workflow.execute_activity_method(
                DeliveryActivities.review_change, args=[inp, spec, code, report, trace_id], **LLM_OPTS)
            _add(usage, rr.usage)
            result.review = rr.review
            result.gate = evaluate_gate(report, rr.review, result.guardrail_violations)
            result.status = "delivered" if result.gate.accepted else "rejected_by_gate"
            return await self._finish(result, wf_id)

        except asyncio.CancelledError:
            result.status = "cancelled"
            await self._finish(result, wf_id)
            raise
        except ActivityError as e:
            cause = e.cause
            result.status = "failed"
            result.error = f"{type(cause).__name__}: {cause}" if cause else str(e)
            return await self._finish(result, wf_id)

    async def _finish(self, result: DeliveryResult, wf_id: str) -> DeliveryResult:
        self._state = result.status
        try:
            result.artifacts_dir = await workflow.execute_activity_method(
                DeliveryActivities.write_artifacts, args=[result, wf_id], **LOCAL_OPTS)
            await workflow.execute_activity_method(DeliveryActivities.finalize_trace, args=[result], **LOCAL_OPTS)
        except ActivityError as e:                      # bookkeeping must never change the delivery outcome
            result.error = (result.error + " | " if result.error else "") + f"finalize failed: {e.cause}"
        return result


@workflow.defn
class EvalBatchWorkflow:
    @workflow.run
    async def run(self, inputs: List[DeliveryInput], max_concurrency: int = 2) -> List[DeliveryResult]:
        sem = asyncio.Semaphore(max(1, max_concurrency))
        base = workflow.info().workflow_id

        async def one(inp: DeliveryInput) -> DeliveryResult:
            async with sem:
                return await workflow.execute_child_workflow(
                    DeliveryWorkflow.run, inp, id=f"{base}/{inp.variant}/{inp.task_id}")

        return list(await asyncio.gather(*[one(i) for i in inputs]))
