"""API keys, one workspace each."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class ApiKeyIn(BaseModel):
    name: str
    scopes: list[str] = Field(default_factory=lambda: ["*"],
                              description='model ids or slugs, or ["*"] for every model '
                                          'in the workspace')
    rate_limit_rpm: int = Field(600, ge=0, le=1_000_000)
    monthly_quota: int = Field(0, ge=0)
    expires_in_days: int | None = Field(None, ge=1, le=3650)


class ApiKeyPatch(BaseModel):
    name: str | None = None
    scopes: list[str] | None = None
    rate_limit_rpm: int | None = Field(None, ge=0, le=1_000_000)
    monthly_quota: int | None = Field(None, ge=0)


class ApiKeyOut(ORMModel):
    id: str
    workspace_id: str
    name: str
    prefix: str
    scopes: list[str]
    rate_limit_rpm: int
    monthly_quota: int
    used_this_month: int = 0
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None


class ApiKeyCreated(ApiKeyOut):
    key: str = Field(description="shown once - Aperture stores only its hash")
