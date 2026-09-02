"""The control channel between runtimes and the control plane.

A runtime heartbeats every few seconds with what it is holding and how loaded it
is; the reply is its desired assignment list. That single exchange covers
service discovery, health (R4.5), load reporting for autoscaling (R4.8) and
rolling deploys (R4.6) without the runtime needing database access.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app import storage
from app.cache import drop_runtime, register_runtime
from app.db import get_session
from app.deps import internal_auth
from app.metrics import queue_depth, runtime_gpu_memory, runtime_up
from app.models import Runtime
from app.services.deployer import apply_runtime_report, assignments_for_runtime, reconcile
from app.settings import settings

log = logging.getLogger("aperture.internal")
router = APIRouter(prefix="/internal/v1", tags=["internal"], dependencies=[Depends(internal_auth)])


class LoadedVersion(BaseModel):
    version_id: str
    state: str = "pending"  # pending | loading | warming | ready | failed | draining
    detail: str = ""
    queue_depth: int = 0
    in_flight: int = 0
    loaded_at: datetime | None = None
    memory_mb: int = 0
    requests: int = 0
    batches: int = 0


class Heartbeat(BaseModel):
    runtime_id: str
    url: str
    hostname: str = ""
    device: str = "cpu"
    gpu_name: str = ""
    build: str = ""
    total_memory_mb: int = 0
    free_memory_mb: int = 0
    slots: int = 4
    status: str = "ready"
    loaded: list[LoadedVersion] = Field(default_factory=list)
    metrics: dict = Field(default_factory=dict)


@router.post("/heartbeat")
async def heartbeat(body: Heartbeat, session: AsyncSession = Depends(get_session)) -> dict:
    now = datetime.now(UTC)
    runtime = await session.get(Runtime, body.runtime_id)
    if runtime is None:
        runtime = Runtime(id=body.runtime_id, url=body.url)
        session.add(runtime)
        log.info("runtime joined", extra={"runtime_id": body.runtime_id, "device": body.device})

    runtime.url = body.url
    runtime.hostname = body.hostname
    runtime.device = body.device
    runtime.gpu_name = body.gpu_name
    runtime.build = body.build
    runtime.total_memory_mb = body.total_memory_mb
    runtime.free_memory_mb = body.free_memory_mb
    runtime.slots = body.slots
    runtime.status = body.status
    runtime.loaded = [item.model_dump(mode="json") for item in body.loaded]
    runtime.metrics = body.metrics
    runtime.last_heartbeat = now
    await session.commit()

    await apply_runtime_report(session, body.runtime_id, runtime.loaded)

    ready = [item.version_id for item in body.loaded if item.state == "ready"]
    await register_runtime(
        body.runtime_id,
        {
            "url": body.url, "device": body.device, "gpu_name": body.gpu_name,
            "status": body.status, "slots": body.slots,
            # which artifact formats this image can load, so anything picking a
            # runtime from the cache can respect it without a database round trip
            "formats": (body.metrics or {}).get("formats") or [],
            "queues": {item.version_id: item.queue_depth for item in body.loaded},
            "in_flight": {item.version_id: item.in_flight for item in body.loaded},
            "loaded": ready,
        },
        ready,
    )

    runtime_up.labels(body.runtime_id, body.device).set(1 if body.status == "ready" else 0)
    if body.total_memory_mb:
        runtime_gpu_memory.labels(body.runtime_id, "total").set(body.total_memory_mb)
        runtime_gpu_memory.labels(body.runtime_id, "free").set(body.free_memory_mb)
    for item in body.loaded:
        queue_depth.labels(body.runtime_id, item.version_id).set(item.queue_depth)

    assignments = await assignments_for_runtime(session, body.runtime_id)
    return {
        "assignments": assignments,
        "heartbeat_interval_s": max(2, settings.runtime_heartbeat_ttl_s // 3),
        "server_time": now.isoformat(),
    }


@router.post("/runtimes/{runtime_id}/leave")
async def leave(runtime_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    """Graceful shutdown: the runtime tells us before it goes, so we re-place its work."""
    runtime = await session.get(Runtime, runtime_id)
    if runtime is not None:
        runtime.status = "stopped"
        runtime.loaded = []
        await session.commit()
    await drop_runtime(runtime_id)
    runtime_up.labels(runtime_id, runtime.device if runtime else "unknown").set(0)
    result = await reconcile(session)
    log.info("runtime left", extra={"runtime_id": runtime_id, **result})
    return {"ok": True, **result}


@router.post("/weights/presign")
async def presign_weights(payload: dict) -> dict:
    """Runtimes pull weights straight from object storage, never through the API."""
    bucket = payload.get("bucket") or settings.s3_bucket_weights
    key = payload.get("key")
    if not key:
        return {"error": "key is required"}
    url = await storage.presign_get(bucket, key, expires=3600)
    return {"url": url, "expires_in": 3600}
