"""The inference log writer.

Requests publish onto a Redis stream and return; this process batches those
records into Postgres. Two reasons it is its own process:

  * an inference response never waits on a database write (R5.1 must not cost latency)
  * a Postgres hiccup degrades observability, not serving - the stream buffers,
    and a capped stream is a bounded memory cost rather than an outage
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
from datetime import datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.cache import LOG_GROUP, LOG_STREAM, client
from app.db import SessionLocal, engine
from app.jobs.heartbeat import LOGSINK_FILE, touch
from app.logging_setup import configure_logging
from app.models import InferenceLog
from app.settings import settings

log = logging.getLogger("aperture.logsink")

GROUP = LOG_GROUP
CONSUMER = "logsink-1"
BATCH = 200
BLOCK_MS = 2000

FIELDS = {c.name for c in InferenceLog.__table__.columns}
_stop = asyncio.Event()


def _coerce(record: dict) -> dict:
    row = {k: v for k, v in record.items() if k in FIELDS}
    for key in ("created_at", "purge_after"):
        value = row.get(key)
        if isinstance(value, str):
            try:
                row[key] = datetime.fromisoformat(value)
            except ValueError:
                row[key] = None
    for key in ("queue_ms", "preprocess_ms", "inference_ms", "postprocess_ms", "gateway_ms",
                "total_ms"):
        row[key] = float(row.get(key) or 0.0)
    for key in ("input_ref", "input_stats", "output", "confidence", "overrides", "capture"):
        if not isinstance(row.get(key), dict):
            row[key] = {}
    row["batch_size"] = int(row.get("batch_size") or 0)
    row["detection_count"] = int(row.get("detection_count") or 0)
    row["http_status"] = int(row.get("http_status") or 200)
    return row


async def _ensure_group() -> None:
    redis = client()
    try:
        await redis.xgroup_create(LOG_STREAM, GROUP, id="0", mkstream=True)
        log.info("created consumer group", extra={"stream": LOG_STREAM})
    except Exception as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def _flush(entries: list[tuple[str, dict]]) -> int:
    rows = []
    for _, fields in entries:
        try:
            rows.append(_coerce(json.loads(fields["payload"])))
        except Exception:
            log.warning("dropping unreadable log record", exc_info=True)
    if not rows:
        return 0
    async with SessionLocal() as session:
        # request_id is not unique (a batch may retry), so plain inserts with
        # on_conflict_do_nothing on the primary key keeps replays idempotent
        await session.execute(
            pg_insert(InferenceLog).on_conflict_do_nothing(index_elements=["id"]), rows
        )
        await session.commit()
    return len(rows)


async def run() -> None:
    configure_logging(settings.log_level)
    await _ensure_group()
    redis = client()
    log.info("log sink started", extra={"stream": LOG_STREAM})

    written = 0
    while not _stop.is_set():
        touch(LOGSINK_FILE)
        try:
            response = await redis.xreadgroup(
                GROUP, CONSUMER, {LOG_STREAM: ">"}, count=BATCH, block=BLOCK_MS
            )
        except Exception:
            log.exception("stream read failed; retrying")
            await asyncio.sleep(2)
            continue

        if not response:
            continue
        for _, entries in response:
            count = await _flush(entries)
            written += count
            await redis.xack(LOG_STREAM, GROUP, *[entry_id for entry_id, _ in entries])
            if written and written % 1000 < count:
                log.info("log sink progress", extra={"records_written": written})

    await engine.dispose()
    log.info("log sink stopped", extra={"records_written": written})


def main() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _stop.set)
    loop.run_until_complete(run())


if __name__ == "__main__":
    main()
