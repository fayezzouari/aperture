"""Registering, editing and listing models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel
from app.schemas.tag import TagOut


class ModelIn(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,94}$")
    name: str
    task: Literal["detection", "classification", "segmentation", "keypoints", "ocr", "embedding"]
    owner: str = ""
    framework: str = "onnx"
    description: str = ""
    labels: list[str] = Field(default_factory=list)


class ModelPatch(BaseModel):
    name: str | None = None
    owner: str | None = None
    description: str | None = None
    labels: list[str] | None = None


class ModelOut(ORMModel):
    id: str
    project_id: str
    slug: str
    name: str
    task: str
    owner: str
    framework: str
    description: str
    labels: list[str]
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None
    version_count: int = 0
    latest_version: int | None = None
    tags: list[TagOut] = Field(default_factory=list)
    deployed_versions: list[int] = Field(default_factory=list)
    replicas_ready: int = 0
    replicas_desired: int = 0
    requests_24h: int = 0
    error_rate_24h: float = 0.0
    p95_ms_24h: float | None = None
