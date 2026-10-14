from adp.gate import evaluate_gate
from adp.models import Review, TestReport

OK = TestReport(passed=True, n_passed=5)
BAD = TestReport(passed=False, n_passed=3, n_failed=2)


def test_all_good_is_accepted():
    g = evaluate_gate(OK, Review(score=4), [])
    assert g.accepted and g.reasons == ["all checks passed"]


def test_failing_tests_reject():
    g = evaluate_gate(BAD, Review(score=5), [])
    assert not g.accepted and "tests failed" in g.reasons[0]


def test_guardrail_violation_rejects_even_if_tests_pass():
    g = evaluate_gate(OK, Review(score=5), ["forbidden import: os"])
    assert not g.accepted and "guardrail" in g.reasons[0]


def test_low_review_score_rejects():
    assert not evaluate_gate(OK, Review(score=2), []).accepted
    assert evaluate_gate(OK, Review(score=3), []).accepted


def test_missing_evidence_rejects():
    assert not evaluate_gate(None, Review(score=5), []).accepted
    assert not evaluate_gate(OK, None, []).accepted


def test_timeout_rejects_with_reason():
    g = evaluate_gate(TestReport(passed=False, timed_out=True), Review(score=5), [])
    assert not g.accepted and "timed out" in g.reasons[0]


def test_multiple_reasons_are_all_reported():
    g = evaluate_gate(BAD, Review(score=1), ["x"])
    assert len(g.reasons) == 3
