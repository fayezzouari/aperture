"""Instance users and their roles, as an admin manages them."""

from __future__ import annotations

from pydantic import BaseModel

from app.schemas.common import Role


class UserInviteIn(BaseModel):
    email: str
    role: Role = "viewer"


class UserPatch(BaseModel):
    role: Role | None = None
    is_active: bool | None = None
