"""Async jobs, with credentials masked on the way out."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORMModel


class AsyncJobIn(BaseModel):
    kind: Literal["bulk_images", "video"] = "bulk_images"
    model: str
    ref: str = "production"
    source_keys: list[str] | None = Field(None, description="object keys already in storage")
    source_urls: list[str] | None = None
    video_key: str | None = None
    frame_stride: int = Field(1, ge=1, le=1000)
    overrides: dict[str, Any] | None = None
    webhook_url: str | None = None


SECRET_PARAM_HINTS = ("api_key", "apikey", "token", "password", "secret")


def redact_secrets(value: Any) -> Any:
    """Mask anything that looks like a credential, at any depth of a params dict."""
    if isinstance(value, dict):
        return {k: ("***" if v and any(h in str(k).lower() for h in SECRET_PARAM_HINTS)
                    else redact_secrets(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [redact_secrets(v) for v in value]
    return value


class JobOut(ORMModel):
    id: str
    kind: str
    status: str
    params: dict
    progress: int
    total: int
    result: dict
    error: str | None
    webhook_url: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @field_validator("params", mode="before")
    @classmethod
    def _redact(cls, value: Any) -> Any:
        # older rows (and any future job kind) may hold a credential; never echo one back
        return redact_secrets(value or {})
