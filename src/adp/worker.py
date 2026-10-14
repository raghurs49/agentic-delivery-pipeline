"""Worker process: hosts the workflows and activities."""
import asyncio
from concurrent.futures import ThreadPoolExecutor

from temporalio.client import Client
from temporalio.worker import Worker

from .activities import DeliveryActivities
from .config import Settings
from .llm import build_provider
from .observability import build_tracer
from .workflows import DeliveryWorkflow, EvalBatchWorkflow


def make_activities(settings: Settings) -> DeliveryActivities:
    return DeliveryActivities(build_provider(settings), build_tracer(settings.langfuse_host), settings)


async def run_worker(settings: Settings) -> None:
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    acts = make_activities(settings)
    with ThreadPoolExecutor(max_workers=4) as pool:
        worker = Worker(client, task_queue=settings.task_queue, workflows=[DeliveryWorkflow, EvalBatchWorkflow],
                        activities=acts.all(), activity_executor=pool, max_concurrent_activities=4)
        print(f"worker up: queue={settings.task_queue} temporal={settings.temporal_address} "
              f"provider={settings.provider} tracing={'langfuse' if acts.tracer.enabled else 'off'}")
        await worker.run()


def main() -> None:
    asyncio.run(run_worker(Settings.from_env()))
