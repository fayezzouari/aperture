"""Multipart weight uploads."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORMModel


class UploadInit(BaseModel):
    version_id: str | None = None
    filename: str
    total_bytes: int = Field(ge=1)
    sha256: str | None = Field(None, min_length=64, max_length=64)
    chunk_bytes: int | None = Field(None, ge=5 * 1024 * 1024)

    @field_validator("filename")
    @classmethod
    def _safe(cls, v: str) -> str:
        if "/" in v or "\\" in v or v.startswith("."):
            raise ValueError("filename must not contain a path")
        return v


class UploadSessionOut(ORMModel):
    id: str
    version_id: str | None = None
    filename: str
    total_bytes: int
    received_bytes: int
    chunk_bytes: int
    status: str
    next_part: int
    received_parts: list[int] = Field(default_factory=list)
    expires_at: datetime | None = None
