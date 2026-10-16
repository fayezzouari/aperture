"""Runtime replicas as the control plane sees them."""

from __future__ import annotations

from datetime import datetime

from app.schemas.common import ORMModel


class RuntimeOut(ORMModel):
    id: str
    url: str
    hostname: str
    device: str
    gpu_name: str
    total_memory_mb: int
    free_memory_mb: int
    slots: int
    status: str
    loaded: list
    metrics: dict
    last_heartbeat: datetime | None
    fresh: bool = True
