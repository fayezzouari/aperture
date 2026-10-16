"""Heartbeat loop: tell the control plane what we hold, learn what we should hold."""

from __future__ import annotations

import asyncio
import logging

import httpx

from runtime.device import refresh_memory
from runtime.settings import settings

log = logging.getLogger("runtime.control")


class ControlPlane:
    def __init__(self, store, device_info: dict):
        from runtime.device import formats as detect_formats

        self.formats = detect_formats()
        self.store = store
        self.device_info = device_info
        self.runtime_id = settings.resolved_runtime_id()
        self.url = settings.resolved_url()
        self.interval = settings.heartbeat_interval_s
        self.connected = False
        self.last_error: str | None = None
        self._task: asyncio.Task | None = None
        self._client = httpx.AsyncClient(
            base_url=settings.control_plane_url.rstrip("/"),
            timeout=httpx.Timeout(connect=3, read=15, write=15, pool=5),
            headers={"x-runtime-token": settings.runtime_internal_token},
        )

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="heartbeat")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        try:
            await self._client.post(f"/internal/v1/runtimes/{self.runtime_id}/leave")
        except Exception:
            log.info("could not announce shutdown; the control plane will time us out")
        await self._client.aclose()

    async def _loop(self) -> None:
        backoff = 1.0
        while True:
            try:
                interval = await self._beat()
                self.connected = True
                self.last_error = None
                backoff = 1.0
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.connected = False
                self.last_error = str(exc)[:300]
                log.warning("heartbeat failed, retrying", extra={"error": self.last_error,
                                                                 "in_seconds": backoff})
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def _beat(self) -> float:
        refresh_memory(self.device_info)
        loaded = self.store.report()
        payload = {
            "runtime_id": self.runtime_id,
            "url": self.url,
            "hostname": settings.advertise_host or "",
            "device": self.device_info.get("device", "cpu"),
            "gpu_name": self.device_info.get("gpu_name", ""),
            "build": settings.build,
            "total_memory_mb": self.device_info.get("total_memory_mb", 0),
            "free_memory_mb": self.device_info.get("free_memory_mb", 0),
            "slots": settings.max_loaded_versions,
            "status": "ready",
            "loaded": loaded,
            "metrics": {
                "providers": self.device_info.get("providers", []),
                "formats": self.formats,
                "loaded_count": len(loaded),
                "infer_workers": settings.infer_workers,
                # host memory, for placement; total/free_memory_mb above are the GPU's
                "memory": self.store.memory(),
            },
        }
        response = await self._client.post("/internal/v1/heartbeat", json=payload)
        response.raise_for_status()
        body = response.json()
        await self.store.apply_assignments(body.get("assignments") or [])
        return float(body.get("heartbeat_interval_s") or self.interval)
