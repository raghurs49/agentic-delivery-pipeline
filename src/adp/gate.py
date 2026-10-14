"""The quality gate: a pure function over the evidence, so it can be unit-tested and replayed."""
from typing import List, Optional

from .models import GateDecision, Review, TestReport


def evaluate_gate(report: Optional[TestReport], review: Optional[Review], violations: List[str],
                  min_review_score: int = 3) -> GateDecision:
    reasons: List[str] = []
    if violations:
        reasons.append(f"{len(violations)} guardrail violation(s): " + "; ".join(violations[:3]))
    if report is None:
        reasons.append("tests were not run")
    else:
        if report.timed_out:
            reasons.append("tests timed out")
        elif not report.passed:
            reasons.append(f"tests failed ({report.n_passed} passed, {report.n_failed} failed)")
    if review is None or review.score < min_review_score:
        reasons.append(f"review score {review.score if review else 'n/a'} below {min_review_score}")
    return GateDecision(accepted=not reasons, reasons=reasons or ["all checks passed"])
