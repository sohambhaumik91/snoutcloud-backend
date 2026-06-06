"""arq durable-queue plumbing.

The web process enqueues inference jobs here; the always-on worker process
(app/worker.py) consumes them. Using arq (asyncio-native, backed by the same
Redis already used for pub/sub) means a job survives a web/worker restart —
unlike the previous FastAPI BackgroundTasks, which died with the process.
"""

from uuid import UUID

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import settings

# Name the worker function once so the enqueue side and the worker registration
# can never drift apart.
INFERENCE_TASK = "run_pipeline_task"

_pool: ArqRedis | None = None


def redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(settings.redis_url)


async def get_arq_pool() -> ArqRedis:
    """Lazily create (and cache) the arq enqueue pool. Mirrors get_redis()."""
    global _pool
    if _pool is None:
        _pool = await create_pool(redis_settings())
    return _pool


async def enqueue_inference(embedding_job_id: UUID | str) -> None:
    pool = await get_arq_pool()
    await pool.enqueue_job(INFERENCE_TASK, str(embedding_job_id))


async def close_arq_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None
