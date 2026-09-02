"""arq queue handle shared by the API and the worker.

arq is the Python analogue of what Oban does for Elixir: durable jobs on a
datastore we already run (Redis), with cron support and no extra broker.
"""

from __future__ import annotations

import logging

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.settings import settings

log = logging.getLogger("aperture.queue")

QUEUE_NAME = "aperture:jobs"
# Conversion needs torch and ultralytics - gigabytes the serving path has no use
# for - so it runs on its own image, watching its own queue.
QUEUE_CONVERT = "aperture:jobs:convert"
_pool: ArqRedis | None = None


def redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(settings.redis_url)


async def pool() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(redis_settings(), default_queue_name=QUEUE_NAME)
    return _pool


async def close() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


async def enqueue(task: str, *args, queue: str = QUEUE_NAME, **kwargs) -> str | None:
    job = await (await pool()).enqueue_job(task, *args, _queue_name=queue, **kwargs)
    if job is None:
        log.warning("job was not enqueued (duplicate id?)", extra={"task": task})
        return None
    return job.job_id


async def worker_alive(queue: str) -> bool:
    """Is anything watching this queue? arq keeps a health key per worker.

    Used to refuse a conversion at submit time instead of letting the job sit
    queued forever with nothing to run it.
    """
    try:
        return bool(await (await pool()).exists(f"{queue}:health-check"))
    except Exception:
        log.warning("could not check worker health", exc_info=True)
        return False
