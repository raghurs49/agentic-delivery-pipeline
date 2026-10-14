"""Command line: run the worker, start/approve/inspect deliveries, and run the evaluation."""
import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

from temporalio.client import Client

from .config import Settings
from .eval_runner import run_eval
from .evals import compare, load_tasks
from .models import ApprovalDecision, DeliveryInput
from .observability import build_tracer
from .workflows import DeliveryWorkflow


async def _client(s: Settings) -> Client:
    return await Client.connect(s.temporal_address, namespace=s.temporal_namespace)


async def cmd_run(a, s: Settings) -> int:
    task = load_tasks(ids=[a.task])[0]
    c = await _client(s)
    wf_id = a.id or f"delivery-{task.id}-{int(time.time())}"
    inp = DeliveryInput(task_id=task.id, requirement=task.requirement, variant=a.variant, auto_approve=a.auto_approve,
                        approval_timeout_s=a.approval_timeout)
    h = await c.start_workflow(DeliveryWorkflow.run, inp, id=wf_id, task_queue=s.task_queue)
    tr = build_tracer(s.langfuse_host)
    from .observability import trace_id_for
    print(f"started {wf_id}  variant={a.variant}  auto_approve={a.auto_approve}")
    if tr.trace_url(trace_id_for(wf_id)):
        print("trace:", tr.trace_url(trace_id_for(wf_id)))
    if not a.auto_approve:
        print(f"waiting for approval. Review the spec with:  adp status {wf_id}\n"
              f"then:  adp approve {wf_id}   (or  adp approve {wf_id} --reject --comment 'why')")
    res = await h.result()
    print(f"\nstatus: {res.status}   iterations: {res.iterations}   llm_calls: {res.usage.llm_calls}")
    if res.gate:
        print("gate:", "ACCEPTED" if res.gate.accepted else "REJECTED", "-", "; ".join(res.gate.reasons))
    if res.artifacts_dir:
        print("artifacts:", res.artifacts_dir)
    return 0 if res.status == "delivered" else 1


async def cmd_status(a, s: Settings) -> int:
    st = await (await _client(s)).get_workflow_handle(a.workflow_id).query(DeliveryWorkflow.status)
    print(f"state: {st.state}   iteration: {st.iteration}")
    if st.spec:
        print(json.dumps(asdict(st.spec), indent=2))
    return 0


async def cmd_approve(a, s: Settings) -> int:
    h = (await _client(s)).get_workflow_handle(a.workflow_id)
    await h.signal(DeliveryWorkflow.approve_spec, ApprovalDecision(approved=not a.reject, reviewer=a.reviewer, comment=a.comment))
    print("sent:", "rejection" if a.reject else "approval")
    return 0


async def cmd_eval(a, s: Settings) -> int:
    variants = [v.strip() for v in a.variants.split(",") if v.strip()]
    ids = None if a.tasks == "all" else [t.strip() for t in a.tasks.split(",")]
    out = await run_eval(await _client(s), s, build_tracer(s.langfuse_host), variants, load_tasks(ids=ids),
                         concurrency=a.concurrency, stamp=a.stamp or None, log=lambda m: print(m, flush=True))
    p = Path(a.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=2))
    print("wrote", p)
    if a.check:
        regress = compare(out, json.loads(Path(a.check).read_text()), hidden_pass_drop=a.tolerance)
        for r in regress:
            print("REGRESSION:", r)
        if regress:
            return 2
        print("no regressions vs", a.check)
    return 0


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="adp")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("worker", help="run the Temporal worker")
    r = sub.add_parser("run", help="deliver one task through the pipeline")
    r.add_argument("task"); r.add_argument("--variant", default="v2-spec-driven"); r.add_argument("--id", default="")
    r.add_argument("--auto-approve", action="store_true"); r.add_argument("--approval-timeout", type=int, default=3600)
    st = sub.add_parser("status", help="show a run's state and spec"); st.add_argument("workflow_id")
    ap = sub.add_parser("approve", help="human checkpoint: approve or reject the spec")
    ap.add_argument("workflow_id"); ap.add_argument("--reject", action="store_true")
    ap.add_argument("--comment", default=""); ap.add_argument("--reviewer", default="human")
    e = sub.add_parser("eval", help="evaluate variants on the task suite with hidden-test ground truth")
    e.add_argument("--variants", default="v1-naive,v2-spec-driven"); e.add_argument("--tasks", default="all")
    e.add_argument("--concurrency", type=int, default=2); e.add_argument("--out", default="evals/results/latest.json")
    e.add_argument("--check", default="", help="baseline results JSON to compare against (exit 2 on regression)")
    e.add_argument("--tolerance", type=float, default=0.0, help="allowed drop in hidden pass rate")
    e.add_argument("--stamp", default="", help="reattach to the batches of an earlier/interrupted run (eval-<stamp>-<variant>)")
    a = p.parse_args(argv)
    s = Settings.from_env()
    if a.cmd == "worker":
        from .worker import main as worker_main
        worker_main()
        return
    handler = {"run": cmd_run, "status": cmd_status, "approve": cmd_approve, "eval": cmd_eval}[a.cmd]
    sys.exit(asyncio.run(handler(a, s)))


if __name__ == "__main__":
    main()
