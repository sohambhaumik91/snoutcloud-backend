"""Always-on arq worker that runs the nose-embedding pipeline.

Run as a separate process (never spawned per request):

    arq app.worker.WorkerSettings

It consumes jobs enqueued by app/services/queue.py::enqueue_inference and
executes app/pipelines/nose_pipeline.py::run_pipeline. The 380MB nose encoder
is warmed once on startup so the first job doesn't pay the load cost.
"""

import asyncio
import logging
from uuid import UUID

from app.ml.encoder import warmup as warmup_nose_encoder
from app.pipelines.nose_pipeline import run_pipeline
from app.services.queue import INFERENCE_TASK, redis_settings

logger = logging.getLogger(__name__)


async def run_pipeline_task(ctx: dict, embedding_job_id: str) -> None:
    """arq entrypoint — thin wrapper so the queue payload stays a bare string."""
    await run_pipeline(UUID(embedding_job_id))


async def on_startup(ctx: dict) -> None:
    # Load the checkpoint into memory once, off the event loop.
    await asyncio.to_thread(warmup_nose_encoder)
    logger.info("nose worker ready")


class WorkerSettings:
    functions = [run_pipeline_task]
    redis_settings = redis_settings()
    on_startup = on_startup
    # A single inference (download + CLAHE + ConvNeXt on CPU for 8 crops) can
    # take a while; give it room before arq considers the job stuck.
    job_timeout = 300
    # Pipeline writes terminal status to the DB itself and is not idempotent on
    # the dogs/dog_embeddings inserts, so do not silently re-run on transient
    # errors — surface failure once.
    max_tries = 1
    keep_result = 3600


# Sanity: the worker must register the exact name the enqueue side uses.
assert run_pipeline_task.__name__ == INFERENCE_TASK
