"""Request-scoped dependencies: who is calling, for which project, with what rights."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import sqlalchemy as sa
from fastapi import Depends, Header, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import check_quota, check_rate_limit
from app.db import get_session
from app.errors import ApiError
from app.metrics import rejected_total
from app.models import ApiKey, Model, Project, User
from app.security import decode_token, load_memberships, require_role, resolve_api_key
from app.settings import settings

PROJECT_HEADER = "x-aperture-project"


@dataclass
class Principal:
    kind: str  # "user" | "api_key"
    id: str
    label: str
    roles: dict[str, str] = field(default_factory=dict)
    is_superadmin: bool = False
    api_key: ApiKey | None = None
    project_id: str | None = None

    def require(self, project_id: str, minimum: str) -> None:
        if self.kind == "api_key":
            # keys never touch the control plane; routes that need a role never accept one
            raise ApiError("forbidden", "this endpoint requires a signed-in user")
        require_role(self.roles, project_id, minimum, superadmin=self.is_superadmin)

    def readable_projects(self) -> list[str] | None:
        """None means 'all projects' (superadmin)."""
        if self.is_superadmin:
            return None
        if self.kind == "api_key" and self.project_id:
            return [self.project_id]
        return list(self.roles)


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return None


async def current_user(
    request: Request, session: AsyncSession = Depends(get_session)
) -> Principal:
    token = _bearer(request) or request.cookies.get("aperture_session")
    if not token:
        raise ApiError("unauthenticated", "sign in to continue")
    claims = decode_token(token)
    user = await session.get(User, claims["sub"])
    if user is None or not user.is_active:
        raise ApiError("unauthenticated", "this account is no longer active")
    roles = await load_memberships(session, user.id)
    return Principal(kind="user", id=user.id, label=user.email, roles=roles,
                     is_superadmin=user.is_superadmin)


async def optional_user(
    request: Request, session: AsyncSession = Depends(get_session)
) -> Principal | None:
    try:
        return await current_user(request, session)
    except ApiError:
        return None


async def inference_principal(
    request: Request, session: AsyncSession = Depends(get_session)
) -> Principal:
    """The inference API accepts an API key (products) or a session (UI playground)."""
    raw_key = request.headers.get("x-api-key") or ""
    bearer = _bearer(request) or ""
    if not raw_key and bearer.startswith("ap_"):
        raw_key = bearer

    if raw_key:
        return await principal_for_key(request, session, raw_key)

    principal = await current_user(request, session)
    return principal


async def principal_for_key(
    request: Request, session: AsyncSession, raw_key: str
) -> Principal:
    """Resolve an API key into a Principal, charging it rate limit and quota.

    Split out of `inference_principal` so a caller that carries its key somewhere
    other than the `x-api-key` header - the Roboflow compatibility route puts it
    in the request body, because that is where Roboflow's clients put it - gets
    the identical limits rather than a second, laxer path.
    """
    key = await resolve_api_key(session, raw_key)
    allowed, remaining, reset = await check_rate_limit(key.id, key.rate_limit_rpm)
    if not allowed:
        rejected_total.labels("rate_limited").inc()
        raise ApiError(
            "rate_limited",
            f"rate limit of {key.rate_limit_rpm} requests/minute exceeded",
            details={"retry_after_seconds": reset, "limit_rpm": key.rate_limit_rpm},
        )
    within, left = await check_quota(key.id, key.monthly_quota)
    if not within:
        rejected_total.labels("quota_exceeded").inc()
        raise ApiError("quota_exceeded",
                       f"monthly quota of {key.monthly_quota} requests is used up",
                       details={"monthly_quota": key.monthly_quota})
    request.state.rate_remaining = remaining
    request.state.quota_remaining = left
    # last_used_at is best-effort: a stale value must never fail a prediction
    await session.execute(
        sa.update(ApiKey).where(ApiKey.id == key.id).values(last_used_at=datetime.now(UTC))
    )
    await session.commit()
    return Principal(kind="api_key", id=key.id, label=key.name, api_key=key,
                     project_id=key.project_id)


async def resolve_project(
    request: Request,
    principal: Principal,
    session: AsyncSession,
    project: str | None = None,
) -> Project:
    """Project comes from the API key, an explicit slug, or the X-Aperture-Project header."""
    if principal.kind == "api_key" and principal.project_id:
        found = await session.get(Project, principal.project_id)
        if found is None:
            raise ApiError("not_found", "the project for this API key no longer exists")
        return found

    slug = project or request.headers.get(PROJECT_HEADER)
    if slug:
        found = (
            await session.execute(sa.select(Project).where(Project.slug == slug))
        ).scalar_one_or_none()
        if found is None:
            raise ApiError("not_found", f"no project with slug '{slug}'")
        return found

    readable = principal.readable_projects()
    stmt = sa.select(Project).where(Project.archived_at.is_(None)).order_by(Project.created_at)
    if readable is not None:
        stmt = stmt.where(Project.id.in_(readable))
    found = (await session.execute(stmt.limit(1))).scalar_one_or_none()
    if found is None:
        raise ApiError("not_found", "you do not have access to any project yet")
    return found


async def project_dep(
    request: Request,
    project: str | None = Query(None, description="project slug; defaults to the X-Aperture-Project header"),
    principal: Principal = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Project:
    return await resolve_project(request, principal, session, project)


async def get_model_or_404(session: AsyncSession, project_id: str, ref: str) -> Model:
    stmt = sa.select(Model).where(Model.project_id == project_id)
    stmt = stmt.where(sa.or_(Model.slug == ref, Model.id == ref))
    found = (await session.execute(stmt)).scalar_one_or_none()
    if found is None:
        raise ApiError("model_not_found", f"no model '{ref}' in this project")
    return found


@dataclass(slots=True)
class Page:
    limit: int
    offset: int


def pagination(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> Page:
    return Page(limit=limit, offset=offset)


def internal_auth(x_runtime_token: str = Header(...)) -> None:
    """Runtimes authenticate with a shared token on the internal control channel."""
    import hmac

    if not hmac.compare_digest(x_runtime_token, settings.runtime_internal_token):
        raise ApiError("unauthenticated", "invalid runtime token")
