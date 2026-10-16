"""Personal access tokens."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    expires_in_days: int | None = Field(
        90, ge=1, le=3650, description="null means the token never expires"
    )


class TokenOut(ORMModel):
    id: str
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None


class TokenCreated(TokenOut):
    token: str = Field(description="shown once - Aperture stores only its hash")
