"""Redis: replica registry, tag resolution cache, rate limits, log stream.

Redis holds only derived or short-lived state. Postgres remains the source of
truth, so a flushed Redis costs a cold cache and nothing else.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import redis.asyncio as aioredis

from app.settings import settings

log = logging.getLogger("aperture.cache")

_pool: aioredis.Redis | None = None

LOG_STREAM = "aperture:inference-log"
LOG_STREAM_MAXLEN = 200_000
LOG_GROUP = "logsink"  # the consumer group the log sink reads the stream through
RR_KEY = "aperture:rr"
TAG_KEY = "aperture:tag"
TAG_TTL_S = 5  # a retag is visible within this window even on a cold gateway


def client() -> aioredis.Redis:
    global _pool
    if _pool is None:
        _pool = aioredis.from_url(
            settings.redis_url, encoding="utf-8", decode_responses=True,
            max_connections=64, health_check_interval=30,
        )
    return _pool


async def close() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


# ------------------------------------------------------------------- registry
# Written by the heartbeat endpoint, read by the gateway on every request.


def _runtime_key(runtime_id: str) -> str:
    return f"aperture:runtime:{runtime_id}"


def _version_key(version_id: str) -> str:
    return f"aperture:version:{version_id}:runtimes"


async def register_runtime(runtime_id: str, payload: dict, ready_versions: list[str]) -> None:
    r = client()
    ttl = settings.runtime_heartbeat_ttl_s
    pipe = r.pipeline()
    pipe.set(_runtime_key(runtime_id), json.dumps(payload), ex=ttl)
    pipe.sadd("aperture:runtimes", runtime_id)
    for version_id in ready_versions:
        pipe.zadd(_version_key(version_id), {runtime_id: time.time()})
        pipe.expire(_version_key(version_id), ttl * 6)
    await pipe.execute()


async def drop_runtime(runtime_id: str) -> None:
    r = client()
    await r.delete(_runtime_key(runtime_id))
    await r.srem("aperture:runtimes", runtime_id)


async def healthy_runtimes_for_version(version_id: str) -> list[dict]:
    """R4.5 - only replicas with a fresh heartbeat *and* the version resident."""
    r = client()
    cutoff = time.time() - settings.runtime_heartbeat_ttl_s
    ids = await r.zrangebyscore(_version_key(version_id), cutoff, "+inf")
    if not ids:
        return []
    raw = await r.mget([_runtime_key(i) for i in ids])
    out = []
    for runtime_id, blob in zip(ids, raw):
        if not blob:
            continue
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if data.get("status") == "ready":
            data["runtime_id"] = runtime_id
            out.append(data)
    return out


async def all_runtimes() -> list[dict]:
    r = client()
    ids = await r.smembers("aperture:runtimes")
    if not ids:
        return []
    raw = await r.mget([_runtime_key(i) for i in ids])
    out = []
    for runtime_id, blob in zip(ids, raw):
        if blob:
            data = json.loads(blob)
            data["runtime_id"] = runtime_id
            out.append(data)
    return out


async def next_replica_index(version_id: str, count: int) -> int:
    """Round-robin cursor shared by every API worker, so load spreads evenly."""
    if count <= 1:
        return 0
    value = await client().incr(f"{RR_KEY}:{version_id}")
    return int(value) % count


# ------------------------------------------------------------ tag resolution


async def cache_tag(model_id: str, tag: str, payload: dict) -> None:
    await client().set(f"{TAG_KEY}:{model_id}:{tag}", json.dumps(payload), ex=TAG_TTL_S)


async def read_tag(model_id: str, tag: str) -> dict | None:
    blob = await client().get(f"{TAG_KEY}:{model_id}:{tag}")
    return json.loads(blob) if blob else None


async def invalidate_tag(model_id: str, tag: str | None = None) -> None:
    """R2.3 - retagging must take effect immediately, not after a TTL."""
    r = client()
    if tag:
        await r.delete(f"{TAG_KEY}:{model_id}:{tag}")
        return
    async for key in r.scan_iter(match=f"{TAG_KEY}:{model_id}:*", count=200):
        await r.delete(key)


# ---------------------------------------------------------------- rate limits


async def check_rate_limit(key_id: str, limit_rpm: int) -> tuple[bool, int, int]:
    """Fixed-window counter. Returns (allowed, remaining, reset_seconds)."""
    if limit_rpm <= 0:
        return True, -1, 60
    r = client()
    window = int(time.time() // 60)
    redis_key = f"aperture:rl:{key_id}:{window}"
    pipe = r.pipeline()
    pipe.incr(redis_key)
    pipe.expire(redis_key, 120)
    used, _ = await pipe.execute()
    reset = 60 - int(time.time() % 60)
    return int(used) <= limit_rpm, max(0, limit_rpm - int(used)), reset


async def check_quota(key_id: str, monthly_quota: int) -> tuple[bool, int]:
    if monthly_quota <= 0:
        return True, -1
    r = client()
    month = time.strftime("%Y-%m", time.gmtime())
    redis_key = f"aperture:quota:{key_id}:{month}"
    used = await r.incr(redis_key)
    await r.expire(redis_key, 60 * 60 * 24 * 40)
    return int(used) <= monthly_quota, max(0, monthly_quota - int(used))


async def quota_usage(key_id: str) -> int:
    month = time.strftime("%Y-%m", time.gmtime())
    value = await client().get(f"aperture:quota:{key_id}:{month}")
    return int(value or 0)


# ------------------------------------------------------------------ log stream


async def publish_log(record: dict[str, Any]) -> None:
    """Hand a finished request to the log sink. Never blocks the response."""
    try:
        await client().xadd(
            LOG_STREAM, {"payload": json.dumps(record, default=str)},
            maxlen=LOG_STREAM_MAXLEN, approximate=True,
        )
    except Exception:
        log.warning("could not publish inference log", exc_info=True)


async def stream_depth() -> int:
    """Records the log sink has not written yet.

    Not the stream's length: written records are acknowledged but stay in the
    stream until MAXLEN trims them, so XLEN only ever grows. The sink's group
    knows what is still to do - what it was handed but has not acknowledged
    (pending), plus what it has not been handed yet (lag).
    """
    try:
        redis = client()
        groups = await redis.xinfo_groups(LOG_STREAM)
        group = next((g for g in groups if _text(g.get("name")) == LOG_GROUP), None)
        if group is None or group.get("lag") is None:
            # no sink has started yet, or Redis is too old to report lag
            return int(await redis.xlen(LOG_STREAM))
        return int(group.get("pending") or 0) + int(group["lag"])
    except aioredis.ResponseError:
        return 0  # the stream does not exist yet: nothing has been logged
    except Exception:
        return -1


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)
