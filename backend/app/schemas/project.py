"""Creating, editing and listing projects."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class ProjectIn(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    name: str
    description: str = ""
    capture_sample_rate: float | None = Field(None, ge=0, le=1)
    capture_retention_days: int | None = Field(None, ge=1, le=3650)


class ProjectPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    capture_sample_rate: float | None = Field(None, ge=0, le=1)
    capture_retention_days: int | None = Field(None, ge=1, le=3650)


class ProjectOut(ORMModel):
    id: str
    slug: str
    name: str
    description: str
    capture_sample_rate: float | None
    capture_retention_days: int | None
    created_at: datetime
    model_count: int = 0
    role: str | None = None
