"""Project memberships and their roles."""

from __future__ import annotations

from pydantic import BaseModel

from app.schemas.common import Role


class MemberIn(BaseModel):
    email: str
    role: Role = "viewer"


class MemberOut(BaseModel):
    user_id: str
    email: str
    name: str
    role: Role
    provider: str
