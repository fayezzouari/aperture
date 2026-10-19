"""Workspaces and the people in them."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel, Role


class WorkspaceIn(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    name: str = Field(min_length=1, max_length=160)
    description: str = ""


class WorkspacePatch(BaseModel):
    """The slug is fixed: it is what headers, scripts and links name the workspace by."""

    name: str | None = Field(None, min_length=1, max_length=160)
    description: str | None = None


class WorkspaceOut(ORMModel):
    id: str
    slug: str
    name: str
    description: str
    created_at: datetime
    role: Role = Field(description="the caller's role in this workspace")
    model_count: int = 0
    member_count: int = 0


class MemberIn(BaseModel):
    email: str
    role: Role = "viewer"


class MemberOut(BaseModel):
    user_id: str
    email: str
    name: str
    role: Role
    provider: str
