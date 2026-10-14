"""Temporal activities: everything with side effects (LLM calls, sandboxed test runs, files, tracing).

Failure semantics:
* transient problems (Ollama unreachable / timeout) raise LLMError -> Temporal retries with backoff;
* a model that cannot produce valid output after a few re-prompts raises a NON-retryable ApplicationError
  (type="InvalidModelOutput"), because retrying the identical request would not help.
"""
import contextvars
import json
import os
import threading
from dataclasses import asdict
from typing import Callable, Optional, TypeVar

from temporalio import activity
from temporalio.exceptions import ApplicationError

from . import guardrails
from .agents import (REVIEW_SYSTEM, IMPL_NAIVE, IMPL_SPEC, TESTS_SYSTEM, code_error, extract_code, get_variant, implement_user,
                     parse_review, parse_spec, review_error, review_user, spec_error, spec_system, tests_user, with_feedback)
from .config import Settings
from .llm import LLMProvider
from .models import (DeliveryInput, DeliveryResult, Implementation, ImplResult, Review, ReviewResult, Spec, SpecResult,
                     TestReport, TestsArtifact, TestsResult, Usage)
from .observability import Tracer
from .sandbox import run_pytest

T = TypeVar("T")


def _workflow_id() -> str:
    try:
        return activity.info().workflow_id
    except Exception:
        return "unit-test"


class DeliveryActivities:
    def __init__(self, provider: LLMProvider, tracer: Tracer, settings: Settings):
        self.provider, self.tracer, self.settings = provider, tracer, settings

    # ---- helpers -------------------------------------------------------------------------------------------
    def _heartbeating(self, fn: Callable[[], T]) -> T:
        """Run a long blocking call while heartbeating, so Temporal can detect a dead worker quickly."""
        ctx = contextvars.copy_context()
        stop = threading.Event()

        def beat() -> None:
            while not stop.wait(5):
                try:
                    ctx.run(activity.heartbeat)
                except Exception:
                    return

        t = threading.Thread(target=beat, daemon=True)
        t.start()
        try:
            return fn()
        finally:
            stop.set()

    def _llm(self, *, stage: str, model: str, system: str, user: str, trace_id: str, name: str, usage: Usage,
             attempt: int = 0, max_tokens: int = 1200):
        stage_key = stage if attempt == 0 else f"{stage}#{attempt}"
        seed = 7 + attempt
        with self.tracer.generation(name, trace_id, model=model,
                                    input=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                                    parameters={"temperature": 0, "seed": seed, "max_tokens": max_tokens}) as gen:
            res = self._heartbeating(lambda: self.provider.generate(
                stage=stage_key, model=model, system=system, user=user, seed=seed, max_tokens=max_tokens))
            gen.update(output=res.text, usage_details={"input": res.prompt_tokens, "output": res.completion_tokens})
        usage.llm_calls += 1
        usage.prompt_tokens += res.prompt_tokens
        usage.completion_tokens += res.completion_tokens
        usage.latency_ms += res.latency_ms
        return res

    def _span(self, name: str, trace_id: str, task_id: str, variant: str, **kw):
        """Every observation carries the trace-level attributes (Langfuse v4 propagates them per observation)."""
        return self.tracer.span(name, trace_id, trace_name=f"delivery:{task_id}", session_id=_workflow_id(),
                                tags=["adp", variant, task_id], **kw)

    @staticmethod
    def _invalid(msg: str) -> ApplicationError:
        return ApplicationError(msg, type="InvalidModelOutput", non_retryable=True)

    # ---- activities ----------------------------------------------------------------------------------------
    @activity.defn
    def generate_spec(self, inp: DeliveryInput, trace_id: str) -> SpecResult:
        v, usage = get_variant(inp.variant), Usage()
        with self._span("generate_spec", trace_id, inp.task_id, inp.variant, input={"requirement": inp.requirement}) as sp:
            user = inp.requirement
            for attempt in range(3):
                res = self._llm(stage="spec", model=self.settings.spec_model, system=spec_system(v), user=user,
                                trace_id=trace_id, name="spec-llm", usage=usage, attempt=attempt)
                spec = parse_spec(res.text)
                if spec:
                    sp.update(output=asdict(spec))
                    return SpecResult(spec, usage)
                user = with_feedback(inp.requirement, spec_error(res.text))   # temperature 0: an identical retry would fail identically
            sp.update(level="ERROR", status_message="no valid spec after 3 attempts")
            raise self._invalid("model produced no valid specification after 3 attempts")

    @activity.defn
    def generate_tests(self, inp: DeliveryInput, spec: Spec, trace_id: str) -> TestsResult:
        """Write tests from the spec. Tests that fail the structural quality check are regenerated (bounded), with the
        specific problems fed back; if they are still poor the violations are kept and the gate will reject the run."""
        usage = Usage()
        with self._span("generate_tests", trace_id, inp.task_id, inp.variant, input=asdict(spec)) as sp:
            base = tests_user(inp.requirement, spec)
            user = base
            best = None
            for attempt in range(3):
                res = self._llm(stage="tests", model=self.settings.code_model, system=TESTS_SYSTEM,
                                user=user, trace_id=trace_id, name="tests-llm",
                                usage=usage, attempt=attempt, max_tokens=1500)
                code = extract_code(res.text)
                if not code:
                    user = with_feedback(base, code_error(res.text))
                    continue
                violations = guardrails.check_tests(code) + guardrails.check_secrets(code)
                cand = TestsArtifact(code=code, n_tests=code.count("def test"), violations=violations)
                if best is None or len(cand.violations) < len(best.violations):
                    best = cand
                if not violations:
                    break
                user = with_feedback(base, "the tests were rejected by the quality check: " + "; ".join(violations))
            if best is None:
                sp.update(level="ERROR", status_message="no test code after 3 attempts")
                raise self._invalid("model produced no test code after 3 attempts")
            sp.update(output={"n_tests": best.n_tests, "violations": best.violations, "attempts": attempt + 1})
            return TestsResult(best, usage)

    @activity.defn
    def implement(self, inp: DeliveryInput, spec: Spec, prev_code: str, failures: str, iteration: int,
                  trace_id: str) -> ImplResult:
        v, usage = get_variant(inp.variant), Usage()
        system = IMPL_NAIVE if v.implement_style == "naive" else IMPL_SPEC
        with self._span(f"implement#{iteration}", trace_id, inp.task_id, inp.variant, input={"iteration": iteration, "repair": bool(failures)}) as sp:
            base = implement_user(v, inp.requirement, spec, prev_code, failures)
            user = base
            for attempt in range(2):
                res = self._llm(stage=f"implement{iteration}", model=self.settings.code_model, system=system,
                                user=user, trace_id=trace_id,
                                name="implement-llm", usage=usage, attempt=attempt, max_tokens=1500)
                code = extract_code(res.text)
                if not code:
                    user = with_feedback(base, code_error(res.text))
                if code:
                    violations = guardrails.check_code(code) + guardrails.check_diff_size(prev_code, code)
                    sp.update(output={"lines": code.count("\n"), "violations": violations})
                    return ImplResult(Implementation(code=code, iteration=iteration, violations=violations), usage)
            sp.update(level="ERROR", status_message="no code after 2 attempts")
            raise self._invalid("model produced no code after 2 attempts")

    @activity.defn
    def run_tests(self, inp: DeliveryInput, code: str, tests_code: str, trace_id: str) -> TestReport:
        with self._span("run_tests", trace_id, inp.task_id, inp.variant, input={"code_lines": code.count("\n"), "tests_lines": tests_code.count("\n")}) as sp:
            report = run_pytest({inp.target_filename: code, "test_solution.py": tests_code},
                                timeout_s=self.settings.sandbox_timeout_s)
            sp.update(output={"passed": report.passed, "n_passed": report.n_passed, "n_failed": report.n_failed,
                              "timed_out": report.timed_out, "duration_s": report.duration_s})
            return report

    @activity.defn
    def review_change(self, inp: DeliveryInput, spec: Spec, code: str, report: TestReport, trace_id: str) -> ReviewResult:
        usage = Usage()
        summary = (f"{report.n_passed} passed, {report.n_failed} failed" + (", timed out" if report.timed_out else ""))
        with self._span("review", trace_id, inp.task_id, inp.variant) as sp:
            base = review_user(spec, code, summary)
            user = base
            for attempt in range(2):
                res = self._llm(stage="review", model=self.settings.review_model, system=REVIEW_SYSTEM,
                                user=user, trace_id=trace_id, name="review-llm",
                                usage=usage, attempt=attempt, max_tokens=500)
                review = parse_review(res.text)
                if review:
                    sp.update(output=asdict(review))
                    return ReviewResult(review, usage)
                user = with_feedback(base, review_error(res.text))
            # A reviewer that cannot answer is not an infrastructure failure; score 0 so the gate rejects.
            sp.update(level="WARNING", status_message="reviewer returned no valid score")
            return ReviewResult(Review(score=0, issues=["reviewer returned no valid score"]), usage)

    @activity.defn
    def write_artifacts(self, result: DeliveryResult, workflow_id: str) -> str:
        d = self.settings.output_path() / result.task_id / workflow_id.replace("/", "_")
        d.mkdir(parents=True, exist_ok=True)       # idempotent: same workflow id -> same directory, files overwritten
        (d / "solution.py").write_text(result.code)
        (d / "test_solution.py").write_text(result.tests_code)
        (d / "spec.json").write_text(json.dumps(asdict(result.spec) if result.spec else {}, indent=2))
        meta = {k: v for k, v in asdict(result).items() if k not in ("code", "tests_code")}
        (d / "result.json").write_text(json.dumps(meta, indent=2, default=str))
        gate = result.gate
        (d / "report.md").write_text(
            f"# {result.task_id} ({result.variant})\n\n- status: **{result.status}**\n- iterations: {result.iterations}\n"
            f"- gate: {'accepted' if gate and gate.accepted else 'rejected'} - {'; '.join(gate.reasons) if gate else 'n/a'}\n"
            f"- trace: {self.tracer.trace_url(result.trace_id) or 'tracing disabled'}\n")
        return str(d)

    @activity.defn
    def finalize_trace(self, result: DeliveryResult) -> None:
        """Attach the outcome to the Langfuse trace as scores so runs can be filtered and compared."""
        t, tid = self.tracer, result.trace_id
        with self._span("finalize", tid, result.task_id, result.variant) as sp:
            sp.update(output={"status": result.status, "gate": asdict(result.gate) if result.gate else None})
        tr = result.test_report
        t.score(tid, "gate_accepted", 1.0 if (result.gate and result.gate.accepted) else 0.0, boolean=True)
        if tr and (tr.n_passed + tr.n_failed):
            t.score(tid, "tests_pass_ratio", tr.n_passed / (tr.n_passed + tr.n_failed))
        if result.review:
            t.score(tid, "review_score", float(result.review.score))
        t.score(tid, "guardrail_violations", float(len(result.guardrail_violations)))
        t.score(tid, "repair_iterations", float(result.iterations))
        t.flush()

    def all(self):
        return [self.generate_spec, self.generate_tests, self.implement, self.run_tests, self.review_change,
                self.write_artifacts, self.finalize_trace]
