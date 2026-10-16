"""Deployments, their replicas and rollbacks."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class DeploymentIn(BaseModel):
    version: int | None = None
    version_id: str | None = None
    mode: Literal["primary", "canary", "shadow"] = "primary"
    traffic_weight: int = Field(100, ge=0, le=100)
    replicas: int = Field(1, ge=0, le=64)
    device: str = "auto"
    max_batch_size: int = Field(8, ge=1, le=128)
    max_wait_ms: int = Field(8, ge=0, le=2000)
    max_queue_depth: int = Field(64, ge=1, le=10000)
    request_timeout_ms: int = Field(30000, ge=100, le=600000)
    autoscale: bool = False
    min_replicas: int = Field(1, ge=0, le=64)
    max_replicas: int = Field(1, ge=1, le=64)
    target_queue_depth: int = Field(8, ge=1, le=1000)


class DeploymentPatch(BaseModel):
    replicas: int | None = Field(None, ge=0, le=64)
    device: str | None = None
    traffic_weight: int | None = Field(None, ge=0, le=100)
    max_batch_size: int | None = Field(None, ge=1, le=128)
    max_wait_ms: int | None = Field(None, ge=0, le=2000)
    max_queue_depth: int | None = Field(None, ge=1, le=10000)
    request_timeout_ms: int | None = Field(None, ge=100, le=600000)
    autoscale: bool | None = None
    min_replicas: int | None = Field(None, ge=0, le=64)
    max_replicas: int | None = Field(None, ge=1, le=64)
    target_queue_depth: int | None = Field(None, ge=1, le=1000)


class ReplicaOut(BaseModel):
    runtime_id: str
    state: str
    detail: str = ""
    device: str = ""
    gpu_name: str = ""
    queue_depth: int = 0
    loaded_at: datetime | None = None
    ready_at: datetime | None = None
    url: str = ""


class DeploymentOut(ORMModel):
    id: str
    model_id: str
    version_id: str
    version: int | None = None
    mode: str
    traffic_weight: int
    replicas: int
    device: str
    max_batch_size: int
    max_wait_ms: int
    max_queue_depth: int
    request_timeout_ms: int
    autoscale: bool
    min_replicas: int
    max_replicas: int
    target_queue_depth: int
    status: str
    status_detail: str
    created_at: datetime
    updated_at: datetime
    replicas_ready: int = 0
    replica_states: list[ReplicaOut] = Field(default_factory=list)


class RollbackIn(BaseModel):
    tag: str = "production"
    to_version: int | None = Field(
        None, description="defaults to the version the tag pointed at before its last move"
    )
