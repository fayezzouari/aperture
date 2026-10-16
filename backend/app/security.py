"""Authentication, API keys, personal access tokens and RBAC.

Three credentials exist:
  * a *session* is a short-lived JWT from SSO/LDAP/password login and drives the UI
  * a *personal access token* (`ap_pat_...`) is a long-lived stand-in for the user
    who issued it - same roles, same audit identity - for agents and scripts
  * an *API key* (`ap_...`) belongs to a product, is scoped to specific models, and
    can only reach the inference API

Nothing lets an API key mutate the registry, and nothing lets a viewer deploy.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from datetime import UTC, datetime, timedelta

import jwt
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ApiError
from app.models import ApiKey, PersonalAccessToken, User
from app.settings import settings

log = logging.getLogger("aperture.security")

ROLE_ORDER = {"viewer": 0, "developer": 1, "admin": 2}
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1
PAT_PREFIX = "ap_pat_"


def aware_utc(value: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes for timezone-aware columns; treat them as UTC."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


# ------------------------------------------------------------------ passwords


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
                        dklen=32)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${dk.hex()}"


def verify_password(password: str, encoded: str | None) -> bool:
    if not encoded:
        return False
    try:
        scheme, n, r, p, salt_hex, hash_hex = encoded.split("$")
        if scheme != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r),
                            p=int(p), dklen=len(bytes.fromhex(hash_hex)))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), hash_hex)


# ----------------------------------------------------------------------- JWT


def issue_token(user: User) -> tuple[str, int]:
    now = int(time.time())
    ttl = settings.jwt_ttl_minutes * 60
    payload = {
        "sub": user.id,
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "iat": now,
        "exp": now + ttl,
        "iss": "aperture",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256"), ttl


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"], issuer="aperture")
    except jwt.ExpiredSignatureError as exc:
        raise ApiError("unauthenticated", "session expired, sign in again") from exc
    except jwt.InvalidTokenError as exc:
        raise ApiError("unauthenticated", "invalid session token") from exc


# ------------------------------------------------------------------ API keys


def generate_api_key() -> tuple[str, str, str]:
    """Returns (full_key, prefix, hash). The full key is shown exactly once."""
    prefix = "ap_" + secrets.token_hex(4)
    secret = secrets.token_urlsafe(32)
    full = f"{prefix}.{secret}"
    return full, prefix, hashlib.sha256(secret.encode()).hexdigest()


def hash_api_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


async def resolve_api_key(session: AsyncSession, raw: str) -> ApiKey:
    if "." not in raw:
        raise ApiError("api_key_invalid", "malformed API key")
    prefix, secret = raw.split(".", 1)
    key = (
        await session.execute(sa.select(ApiKey).where(ApiKey.prefix == prefix))
    ).scalar_one_or_none()
    if key is None or not hmac.compare_digest(key.key_hash, hash_api_secret(secret)):
        raise ApiError("api_key_invalid", "API key not recognised")
    if key.revoked_at is not None:
        raise ApiError("api_key_revoked", "this API key was revoked")
    expires = aware_utc(key.expires_at)
    if expires is not None and expires < datetime.now(UTC):
        raise ApiError("api_key_expired", "this API key has expired")
    return key


def key_allows_model(key: ApiKey, model_id: str, model_slug: str) -> bool:
    """R7.1 - a key reaches only the models it was issued for."""
    scopes = key.scopes or []
    if not scopes or "*" in scopes:
        return True
    return model_id in scopes or model_slug in scopes


# ------------------------------------------------------- personal access tokens


def is_pat(raw: str | None) -> bool:
    return bool(raw) and raw.startswith(PAT_PREFIX)


def generate_pat() -> tuple[str, str, str]:
    """Returns (full_token, prefix, hash). Same shape as an API key, distinct prefix."""
    prefix = PAT_PREFIX + secrets.token_hex(4)
    secret = secrets.token_urlsafe(32)
    return f"{prefix}.{secret}", prefix, hash_api_secret(secret)


async def resolve_pat(session: AsyncSession, raw: str) -> tuple[PersonalAccessToken, User]:
    """Resolve a personal access token to its row and owner, or raise a 401.

    One query: the owner is joined in because every caller needs both, and the
    inactive-owner check has to happen here so a deactivated account cannot keep
    acting through tokens it minted earlier.
    """
    if "." not in raw:
        raise ApiError("token_invalid", "malformed personal access token")
    prefix, secret = raw.split(".", 1)
    row = (
        await session.execute(
            sa.select(PersonalAccessToken, User)
            .join(User, User.id == PersonalAccessToken.user_id)
            .where(PersonalAccessToken.prefix == prefix)
        )
    ).first()
    if row is None:
        raise ApiError("token_invalid", "personal access token not recognised")
    token, user = row
    if not hmac.compare_digest(token.token_hash, hash_api_secret(secret)):
        raise ApiError("token_invalid", "personal access token not recognised")
    if token.revoked_at is not None:
        raise ApiError("token_revoked", "this personal access token was revoked")
    expires = aware_utc(token.expires_at)
    if expires is not None and expires < datetime.now(UTC):
        raise ApiError("token_expired", "this personal access token has expired")
    if not user.is_active:
        raise ApiError("unauthenticated", "this account is no longer active")
    return token, user


# --------------------------------------------------------------------- RBAC


def require_role(role: str | None, minimum: str) -> None:
    if ROLE_ORDER.get(role or "", -1) < ROLE_ORDER[minimum]:
        raise ApiError(
            "insufficient_role",
            f"this action needs the '{minimum}' role, your role is '{role or 'none'}'",
        )


def expires_at(days: int | None) -> datetime | None:
    return datetime.now(UTC) + timedelta(days=days) if days else None
