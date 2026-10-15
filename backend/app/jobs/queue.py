"""arq queue handle shared by the API and the worker.

arq is the Python analogue of what Oban does for Elixir: durable jobs on a
datastore we already run (Redis), with cron support and no extra broker.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.errors import ApiError
from app.settings import settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models import Job

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


async def dispatch(session: AsyncSession, job: Job, task: str, *,
                   queue: str = QUEUE_NAME, **task_kwargs) -> bool:
    """Hand a committed Job row to the worker; False if the queue would not take it.

    The caller commits before calling this. A worker picks the task up within
    milliseconds and looks the row up by id, so enqueueing inside the still-open
    transaction races the commit: the worker finds nothing, logs "job vanished",
    and the row sits at queued forever. If Redis refuses the task, the row is
    marked failed instead, so nobody waits on a job no worker will ever see.
    """
    try:
        await enqueue(task, job.id, queue=queue, **task_kwargs)
        return True
    except Exception as exc:
        log.exception("could not enqueue job", extra={"task": task, "job_id": job.id})
        job.status = "failed"
        job.error = f"the job queue did not accept this job: {exc}"
        job.finished_at = datetime.now(UTC)
        await session.commit()
        return False


async def submit(session: AsyncSession, job: Job, task: str, *,
                 queue: str = QUEUE_NAME, **task_kwargs) -> None:
    """`dispatch` for a request handler: an unreachable queue is the caller's error."""
    if not await dispatch(session, job, task, queue=queue, **task_kwargs):
        raise ApiError("runtime_unhealthy",
                       "the job queue is unreachable, so the job was recorded as failed; "
                       "check that Redis and the worker are running, then submit it again",
                       details={"job_id": job.id})


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
