"""Run the evaluation suite through Temporal and score it against the hidden tests."""
import asyncio
import time
from typing import Any, Dict, List, Optional

from temporalio.client import Client, WorkflowHandle
from temporalio.service import RPCError, RPCStatusCode

from .config import Settings
from .evals import Task, compute_metrics, score_hidden, task_row
from .models import DeliveryInput
from .observability import Tracer
from .workflows import EvalBatchWorkflow


RETRYABLE = {RPCStatusCode.DEADLINE_EXCEEDED, RPCStatusCode.UNAVAILABLE}


async def result_with_retry(handle: WorkflowHandle, attempts: int = 30, delay_s: float = 2.0):
    """Wait for a workflow result, tolerating the client-side long-poll deadlines a busy Temporal server returns.
    The workflow itself is durable and keeps running; only this waiting call is retried."""
    for i in range(attempts):
        try:
            return await handle.result()
        except RPCError as e:
            if e.status not in RETRYABLE or i == attempts - 1:
                raise
            await asyncio.sleep(delay_s)


async def start_or_attach(client: Client, wf_id: str, inputs: List[Any], concurrency: int, queue: str) -> WorkflowHandle:
    """Reattach to a batch that already exists (running or finished) instead of starting a duplicate."""
    handle = client.get_workflow_handle_for(EvalBatchWorkflow.run, wf_id)   # typed: the result is List[DeliveryResult]
    try:
        await handle.describe()
        return handle
    except RPCError as e:
        if e.status != RPCStatusCode.NOT_FOUND:
            raise
    return await client.start_workflow(EvalBatchWorkflow.run, args=[inputs, concurrency], id=wf_id, task_queue=queue)


async def run_eval(client: Client, settings: Settings, tracer: Tracer, variants: List[str], tasks: List[Task],
                   concurrency: int = 2, stamp: Optional[str] = None, log=print) -> Dict[str, Any]:
    stamp = stamp or time.strftime("%Y%m%d-%H%M%S")
    out: Dict[str, Any] = {
        "meta": {"created": time.strftime("%Y-%m-%dT%H:%M:%S"), "provider": settings.provider,
                 "spec_model": settings.spec_model, "code_model": settings.code_model,
                 "review_model": settings.review_model, "n_tasks": len(tasks)},
        "variants": {},
    }
    by_id = {t.id: t for t in tasks}
    for v in variants:
        inputs = [DeliveryInput(task_id=t.id, requirement=t.requirement, variant=v, auto_approve=True) for t in tasks]
        log(f"[{v}] running {len(inputs)} tasks (concurrency {concurrency}) ...")
        t0 = time.time()
        handle = await start_or_attach(client, f"eval-{stamp}-{v}", inputs, concurrency, settings.task_queue)
        results = await result_with_retry(handle)
        rows = []
        for r in results:
            hidden = score_hidden(by_id[r.task_id], r.code, timeout_s=settings.sandbox_timeout_s)
            rows.append(task_row(r, hidden))
            # attach the ground-truth outcome to the same Langfuse trace as the run itself
            tracer.score(r.trace_id, "hidden_tests_passed", 1.0 if hidden else 0.0, boolean=True,
                         comment=f"{v}/{r.task_id}: ground-truth hidden tests")
        tracer.flush()
        m = compute_metrics(rows)
        out["variants"][v] = {"metrics": m, "tasks": rows}
        log(f"[{v}] done in {time.time() - t0:.0f}s: hidden pass {m['hidden_pass']}/{m['n_tasks']}  gate accepted "
            f"{m['gate_accepted']}  false accepts {m['false_accepts']}  false rejects {m['false_rejects']}")
    return out
