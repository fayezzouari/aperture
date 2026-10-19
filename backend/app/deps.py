"""Request-scoped dependencies: who is calling, in which workspace, with what rights."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from fastapi import Depends, Header, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit
from app.cache import check_quota, check_rate_limit
from app.db import get_session
from app.errors import ApiError
from app.metrics import rejected_total
from app.models import ApiKey, Model, PersonalAccessToken, User, Workspace, WorkspaceMember
from app.security import (
    aware_utc,
    decode_token,
    is_pat,
    require_role,
    resolve_api_key,
    resolve_pat,
)
from app.settings import settings

WORKSPACE_HEADER = "x-aperture-workspace"


@dataclass
class Principal:
    kind: str  # "user" | "api_key"
    id: str
    label: str
    role: str | None = None  # the instance role; None for an API key
    api_key: ApiKey | None = None
    token: PersonalAccessToken | None = None  # set when a personal access token signed in
    # set by the workspace dependencies; while set, `require` checks the member role
    workspace: Workspace | None = None
    workspace_role: str | None = None

    @property
    def actor_type(self) -> str:
        """What the audit log records: user, user:token or api_key."""
        if self.kind == "api_key":
            return "api_key"
        return "user:token" if self.token is not None else "user"

    @property
    def is_admin(self) -> bool:
        return self.kind == "user" and self.role == "admin"

    @property
    def workspace_id(self) -> str | None:
        return self.workspace.id if self.workspace else None

    def require(self, minimum: str) -> None:
        """The role inside the active workspace when there is one, else the instance role."""
        if self.kind == "api_key":
            # keys never touch the control plane; routes that need a role never accept one
            raise ApiError("forbidden", "this endpoint requires a signed-in user")
        require_role(self.workspace_role if self.workspace else self.role, minimum)

    def require_instance(self, minimum: str) -> None:
        """The instance role, whatever workspace is active."""
        if self.kind == "api_key":
            raise ApiError("forbidden", "this endpoint requires a signed-in user")
        require_role(self.role, minimum)


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return None


async def current_user(
    request: Request, session: AsyncSession = Depends(get_session)
) -> Principal:
    bearer = _bearer(request)
    if is_pat(bearer):
        # only the header: a token is never a cookie
        return await principal_for_token(session, bearer)
    token = bearer or request.cookies.get("aperture_session")
    if not token:
        raise ApiError("unauthenticated", "sign in to continue")
    claims = decode_token(token)
    user = await session.get(User, claims["sub"])
    if user is None or not user.is_active:
        raise ApiError("unauthenticated", "this account is no longer active")
    # the role is read from the row, not the token, so a role change applies at once
    return Principal(kind="user", id=user.id, label=user.email, role=user.role)


PAT_LAST_USED_GRANULARITY = timedelta(minutes=1)


async def principal_for_token(session: AsyncSession, raw: str) -> Principal:
    """A personal access token is the user who issued it, with that user's roles today.

    The role is read from the user row on every call rather than frozen into the
    token, so a role change or a deactivation takes effect on the next request.
    """
    token, user = await resolve_pat(session, raw)
    # last_used_at is bookkeeping: written at most once a minute per token, and a
    # failure to write it must never fail the request it rides on.
    now = datetime.now(UTC)
    last = aware_utc(token.last_used_at)
    if last is None or now - last > PAT_LAST_USED_GRANULARITY:
        await session.execute(
            sa.update(PersonalAccessToken)
            .where(PersonalAccessToken.id == token.id)
            .values(last_used_at=now)
        )
        await session.commit()
    audit.actor_type_var.set("user:token")
    return Principal(kind="user", id=user.id, label=user.email, role=user.role, token=token)


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
    if not raw_key and bearer.startswith("ap_") and not is_pat(bearer):
        raw_key = bearer

    if raw_key:
        principal = await principal_for_key(request, session, raw_key)
        return await key_workspace(request, principal, session)

    principal = await current_user(request, session)
    return await enter_workspace(request, principal, session)


async def key_workspace(request: Request, principal: Principal,
                        session: AsyncSession) -> Principal:
    """An API key acts in the workspace it belongs to, and only there.

    The header is optional for a key; naming a different workspace is refused
    rather than ignored, so a misconfigured client finds out at once.
    """
    workspace = await session.get(Workspace, principal.api_key.workspace_id)
    if workspace is None:
        raise ApiError("api_key_invalid", "the workspace of this API key no longer exists")
    named = request.headers.get(WORKSPACE_HEADER)
    if named and named != workspace.slug:
        raise ApiError("forbidden", f"this API key belongs to workspace '{workspace.slug}', "
                                    f"not '{named}'")
    principal.workspace = workspace
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
    return Principal(kind="api_key", id=key.id, label=key.name, api_key=key)


async def workspace_role(session: AsyncSession, user_id: str, instance_role: str | None,
                         workspace_id: str) -> str | None:
    """An instance admin is admin everywhere; anyone else needs a membership."""
    if instance_role == "admin":
        return "admin"
    return (
        await session.execute(
            sa.select(WorkspaceMember.role).where(WorkspaceMember.user_id == user_id,
                                                  WorkspaceMember.workspace_id == workspace_id)
        )
    ).scalar_one_or_none()


def readable_workspaces(principal: Principal):
    """The workspaces a user can open, as a select; every one for an instance admin."""
    stmt = sa.select(Workspace)
    if not principal.is_admin:
        stmt = stmt.where(
            Workspace.id.in_(
                sa.select(WorkspaceMember.workspace_id)
                .where(WorkspaceMember.user_id == principal.id)
            )
        )
    return stmt


async def enter_workspace(request: Request, principal: Principal, session: AsyncSession,
                          slug: str | None = None) -> Principal:
    """Set the workspace a user's request acts in.

    It comes from the `workspace` query parameter or the X-Aperture-Workspace
    header; without either, the workspace the user switched to last, and failing
    that the oldest one they can open. Naming a workspace is a request, not a
    grant: the caller still has to be a member of it.
    """
    slug = slug or request.headers.get(WORKSPACE_HEADER)
    if slug:
        found = (
            await session.execute(sa.select(Workspace).where(Workspace.slug == slug))
        ).scalar_one_or_none()
        if found is None:
            raise ApiError("not_found", f"no workspace '{slug}'")
        role = await workspace_role(session, principal.id, principal.role, found.id)
        if role is None:
            raise ApiError("forbidden", f"you are not a member of workspace '{slug}'")
        principal.workspace, principal.workspace_role = found, role
        return principal

    readable = readable_workspaces(principal).subquery()
    user = await session.get(User, principal.id)
    found = None
    if user is not None and user.last_workspace_id:
        found = (
            await session.execute(
                sa.select(Workspace).where(Workspace.id == user.last_workspace_id,
                                           Workspace.id.in_(sa.select(readable.c.id)))
            )
        ).scalar_one_or_none()
    if found is None:
        found = (
            await session.execute(
                readable_workspaces(principal)
                .order_by(Workspace.created_at, Workspace.slug).limit(1)
            )
        ).scalar_one_or_none()
    if found is None:
        raise ApiError("no_workspace", "you are not a member of any workspace yet")
    principal.workspace = found
    principal.workspace_role = await workspace_role(session, principal.id, principal.role,
                                                    found.id)
    return principal


async def workspace_user(
    request: Request,
    workspace: str | None = Query(
        None, description="workspace slug; defaults to the X-Aperture-Workspace header"
    ),
    principal: Principal = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Principal:
    """A signed-in user acting inside one workspace."""
    return await enter_workspace(request, principal, session, workspace)


async def get_model_or_404(session: AsyncSession, ref: str, workspace_id: str | None) -> Model:
    """A model by slug or id, inside the caller's workspace."""
    found = (
        await session.execute(
            sa.select(Model).where(Model.workspace_id == workspace_id,
                                   sa.or_(Model.slug == ref, Model.id == ref))
        )
    ).scalar_one_or_none()
    if found is None:
        raise ApiError("model_not_found", f"no model '{ref}' in this workspace")
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
