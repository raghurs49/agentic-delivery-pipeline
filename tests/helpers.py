"""Shared test scaffolding: a scripted fake model and a runner that hosts a worker around one scenario."""
import asyncio
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, Optional

from temporalio.client import Client
from temporalio.worker import Worker

from adp.activities import DeliveryActivities
from adp.config import Settings
from adp.llm import FakeProvider, LLMError
from adp.models import DeliveryInput
from adp.observability import NullTracer
from adp.workflows import DeliveryWorkflow, EvalBatchWorkflow

REQ = "Implement add(a, b) returning the sum of two integers."
SPEC_JSON = json.dumps({"summary": "Add two integers.", "signature": "def add(a: int, b: int) -> int",
                        "acceptance_criteria": ["returns the sum", "works for negatives"],
                        "edge_cases": ["add(0, 0) == 0", "add(-1, 1) == 0"]})
TESTS = ("from solution import add\n\ndef test_sum():\n    assert add(1, 2) == 3\n    assert add(0, 0) == 0\n\n"
         "def test_negative():\n    assert add(-1, 1) == 0\n    assert add(-2, -3) == -5\n")
GOOD = "def add(a, b):\n    return a + b\n"
BUGGY = "def add(a, b):\n    return a - b\n"
REVIEW_OK = json.dumps({"score": 5, "issues": []})


def block(code: str) -> str:
    return "```python\n" + code + "\n```"


def make_responder(overrides: Optional[Dict[str, Callable[[int], str]]] = None):
    """Default: everything works. `overrides[stage_prefix](n)` customises the n-th call of that stage."""
    counts: Dict[str, int] = {}
    overrides = overrides or {}

    def respond(stage: str, system: str, user: str, idx: int) -> str:
        key = stage.split("#")[0].rstrip("0123456789")
        n = counts.get(key, 0)
        counts[key] = n + 1
        if key in overrides:
            return overrides[key](n)
        if key == "spec":
            return SPEC_JSON
        if key == "tests":
            return block(TESTS)
        if key == "implement":
            return block(GOOD)
        if key == "review":
            return REVIEW_OK
        raise AssertionError(f"unexpected stage {stage}")
    return respond


class Harness:
    def __init__(self, address: str, tmp_path, provider: FakeProvider):
        self.address, self.tmp_path, self.provider = address, tmp_path, provider
        self.queue = f"test-{uuid.uuid4().hex[:8]}"
        self.settings = Settings(output_dir=str(tmp_path / "runs"), task_queue=self.queue, sandbox_timeout_s=20)
        self.client: Optional[Client] = None

    def inp(self, **kw) -> DeliveryInput:
        d = dict(task_id="add", requirement=REQ, variant="v2-spec-driven", auto_approve=True)
        d.update(kw)
        return DeliveryInput(**d)

    def wf_id(self) -> str:
        return f"wf-{uuid.uuid4().hex[:8]}"


def run_scenario(address, tmp_path, responder, scenario):
    """Run `await scenario(harness)` with a worker hosting the pipeline against a fake model."""
    async def main():
        provider = FakeProvider(responder)
        h = Harness(address, tmp_path, provider)
        h.client = await Client.connect(address)
        acts = DeliveryActivities(provider, NullTracer(), h.settings)
        with ThreadPoolExecutor(max_workers=4) as pool:
            async with Worker(h.client, task_queue=h.queue, workflows=[DeliveryWorkflow, EvalBatchWorkflow],
                              activities=acts.all(), activity_executor=pool):
                return await scenario(h)
    return asyncio.run(main())
