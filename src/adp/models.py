"""Data contracts passed between the workflow and its activities.

Plain dataclasses (no `from __future__ import annotations`) so Temporal's default JSON data converter can
rebuild nested types from the type hints.
"""
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class DeliveryInput:
    task_id: str
    requirement: str
    variant: str = "v2-spec-driven"
    target_filename: str = "solution.py"
    auto_approve: bool = False
    approval_timeout_s: int = 3600


@dataclass
class Spec:
    summary: str = ""
    signature: str = ""
    acceptance_criteria: List[str] = field(default_factory=list)
    edge_cases: List[str] = field(default_factory=list)


@dataclass
class ApprovalDecision:
    approved: bool
    reviewer: str = "human"
    comment: str = ""
    edited_spec: Optional[Spec] = None


@dataclass
class Implementation:
    code: str
    iteration: int = 0
    violations: List[str] = field(default_factory=list)


@dataclass
class TestsArtifact:
    __test__ = False
    code: str
    n_tests: int = 0
    violations: List[str] = field(default_factory=list)


@dataclass
class TestReport:
    __test__ = False
    passed: bool
    n_passed: int = 0
    n_failed: int = 0
    timed_out: bool = False
    output: str = ""
    duration_s: float = 0.0


@dataclass
class Review:
    score: int = 0                     # 1 (poor) .. 5 (excellent)
    issues: List[str] = field(default_factory=list)


@dataclass
class GateDecision:
    accepted: bool
    reasons: List[str] = field(default_factory=list)


@dataclass
class Usage:
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0


@dataclass
class DeliveryResult:
    task_id: str
    variant: str
    status: str                        # delivered | rejected_by_gate | rejected_by_reviewer | approval_timeout | failed | cancelled
    trace_id: str = ""
    spec: Optional[Spec] = None
    code: str = ""
    tests_code: str = ""
    iterations: int = 0
    test_report: Optional[TestReport] = None
    review: Optional[Review] = None
    guardrail_violations: List[str] = field(default_factory=list)
    gate: Optional[GateDecision] = None
    usage: Usage = field(default_factory=Usage)
    artifacts_dir: str = ""
    error: str = ""


@dataclass
class StatusView:
    state: str
    spec: Optional[Spec] = None
    iteration: int = 0
    detail: str = ""


# Activity result wrappers: the payload plus what the LLM calls cost.
@dataclass
class SpecResult:
    spec: Spec
    usage: Usage = field(default_factory=Usage)


@dataclass
class ImplResult:
    impl: Implementation
    usage: Usage = field(default_factory=Usage)


@dataclass
class TestsResult:
    __test__ = False
    tests: TestsArtifact
    usage: Usage = field(default_factory=Usage)


@dataclass
class ReviewResult:
    review: Review
    usage: Usage = field(default_factory=Usage)
