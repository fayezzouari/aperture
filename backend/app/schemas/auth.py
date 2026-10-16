"""Sign-in requests, session tokens and the signed-in user."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.schemas.common import ORMModel, Role


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class UserOut(ORMModel):
    id: str
    email: str
    name: str
    provider: str
    role: Role = "viewer"
    is_active: bool
    last_login_at: datetime | None = None


class AuthMethods(BaseModel):
    password: bool
    oidc: bool
    ldap: bool
    oidc_login_url: str | None = None
    instance_name: str
