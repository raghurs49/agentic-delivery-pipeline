from adp.evals import compare, compute_metrics, wilson


def row(tid, accepted, hidden, **kw):
    d = dict(task_id=tid, status="delivered" if accepted else "rejected_by_gate", gate_accepted=accepted,
             hidden_passed=hidden, iterations=0, guardrail_violations=0, review_score=4, gate_reasons=[], error="",
             trace_id="", llm_calls=4, tokens=100, llm_latency_ms=2000)
    d.update(kw)
    return d


def test_metrics_confusion_matrix():
    rows = [row("a", True, True), row("b", True, False), row("c", False, True), row("d", False, False)]
    m = compute_metrics(rows)
    assert m["n_tasks"] == 4 and m["hidden_pass"] == 2 and m["hidden_pass_rate"] == 0.5
    assert m["gate_accepted"] == 2 and m["false_accepts"] == 1 and m["false_rejects"] == 1
    assert m["gate_precision"] == 0.5
    assert m["statuses"] == {"delivered": 2, "rejected_by_gate": 2}


def test_metrics_with_no_accepted_has_no_precision():
    m = compute_metrics([row("a", False, False)])
    assert m["gate_precision"] is None and m["false_accepts"] == 0


def test_metrics_empty():
    assert compute_metrics([])["n_tasks"] == 0


def test_wilson_interval_bounds():
    lo, hi = wilson(5, 10)
    assert 0.2 < lo < 0.5 < hi < 0.8
    assert wilson(0, 0) == [0.0, 0.0] and wilson(10, 10)[1] == 1.0


def _run(rows):
    return {"variants": {"v": {"metrics": compute_metrics(rows), "tasks": rows}}}


def test_compare_flags_pass_rate_drop_and_task_regression():
    base = _run([row("a", True, True), row("b", True, True)])
    cur = _run([row("a", True, True), row("b", False, False)])
    msgs = compare(cur, base)
    assert any("hidden pass rate fell" in m for m in msgs)
    assert any("v/b: passed in baseline, fails now" in m for m in msgs)


def test_compare_flags_new_false_accepts():
    base = _run([row("a", True, True)])
    cur = _run([row("a", True, False)])
    assert any("false accepts rose" in m for m in compare(cur, base))


def test_compare_is_quiet_when_equal_or_better():
    base = _run([row("a", True, True), row("b", False, False)])
    assert compare(base, base) == []
    better = _run([row("a", True, True), row("b", True, True)])
    assert compare(better, base) == []


def test_compare_tolerance_and_missing_variants():
    base = _run([row("a", True, True), row("b", True, True)])
    cur = _run([row("a", True, True), row("b", True, False)])
    assert compare(cur, base, hidden_pass_drop=0.6, allow_new_false_accepts=1) == ["v/b: passed in baseline, fails now"]
    assert compare({"variants": {"other": cur["variants"]["v"]}}, base) == []


def test_result_with_retry_tolerates_deadline_errors_but_not_others():
    import asyncio

    from temporalio.service import RPCError, RPCStatusCode

    from adp.eval_runner import result_with_retry

    class Handle:
        def __init__(self, errors, value="ok"):
            self.errors, self.value, self.calls = list(errors), value, 0

        async def result(self):
            self.calls += 1
            if self.errors:
                raise RPCError("x", self.errors.pop(0), b"")
            return self.value

    h = Handle([RPCStatusCode.DEADLINE_EXCEEDED, RPCStatusCode.UNAVAILABLE])
    assert asyncio.run(result_with_retry(h, delay_s=0)) == "ok" and h.calls == 3
    bad = Handle([RPCStatusCode.PERMISSION_DENIED])
    try:
        asyncio.run(result_with_retry(bad, delay_s=0))
        raise AssertionError("should have raised")
    except RPCError as e:
        assert e.status == RPCStatusCode.PERMISSION_DENIED and bad.calls == 1
    stuck = Handle([RPCStatusCode.DEADLINE_EXCEEDED] * 5)
    try:
        asyncio.run(result_with_retry(stuck, attempts=3, delay_s=0))
        raise AssertionError("should have raised")
    except RPCError:
        assert stuck.calls == 3
