"""arq worker entrypoint: `arq app.jobs.worker.WorkerSettings`."""

from __future__ import annotations

import logging

from arq import cron

from app.jobs import tasks
from app.jobs.queue import QUEUE_CONVERT, QUEUE_NAME, redis_settings
from app.logging_setup import configure_logging
from app.settings import settings

log = logging.getLogger("aperture.worker")


async def startup(ctx: dict) -> None:
    configure_logging(settings.log_level)
    log.info("worker started", extra={"queue": ctx.get("queue_name", QUEUE_NAME)})


async def shutdown(ctx: dict) -> None:
    from app.db import engine

    await engine.dispose()
    log.info("worker stopped")


class WorkerSettings:
    redis_settings = redis_settings()
    queue_name = QUEUE_NAME
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 8
    job_timeout = 60 * 60 * 6      # a long video is a long job
    keep_result = 60 * 60
    max_tries = 3

    functions = [
        tasks.run_inference_job,
        tasks.export_flagged_dataset,
        tasks.purge_captures,
        tasks.run_report_schedule,
    ]

    cron_jobs = [
        cron(tasks.heartbeat_tick, second={0, 30}, run_at_startup=True),
        # keep the fleet converging even if no deployment call happens
        cron(tasks.reconcile_tick, second={0, 15, 30, 45}, run_at_startup=True),
        cron(tasks.autoscale_tick, second={5, 35}),
        cron(tasks.evaluate_alerts, second={10}),
        cron(tasks.report_scheduler, second={20}),
        cron(tasks.drift_rollup, minute={4}),
        cron(tasks.retention_sweep, minute={17}),
    ]


class ConverterSettings:
    """The optional converter: same code, an image that carries torch and ultralytics.

    It also runs the Roboflow import, which needs the same heavy dependencies -
    the download is small, but what follows it is an ordinary export.

    It watches its own queue so the default worker - which has neither - never
    picks up a conversion it cannot perform.
    """

    redis_settings = redis_settings()
    queue_name = QUEUE_CONVERT
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 1              # an export is CPU- and memory-hungry; one at a time
    job_timeout = 60 * 60
    keep_result = 60 * 60
    max_tries = 1             # a failed export fails for a reason; retrying wastes minutes
    functions = [tasks.convert_version, tasks.import_roboflow]
    cron_jobs = [cron(tasks.converter_heartbeat_tick, second={0, 30}, run_at_startup=True)]
