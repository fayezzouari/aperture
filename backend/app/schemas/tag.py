"""Version tags such as production and staging."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class TagOut(ORMModel):
    id: str
    name: str
    version_id: str
    version: int | None = None
    previous_version_id: str | None = None
    locked: bool
    updated_at: datetime
    updated_by: str | None = None


class TagIn(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,62}$")
    version: int | None = None
    version_id: str | None = None


class TagPatch(BaseModel):
    version: int | None = None
    version_id: str | None = None
    locked: bool | None = None
