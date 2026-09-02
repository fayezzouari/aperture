"""R7.2 - session auth: password, OIDC or LDAP, all mapped onto the same RBAC roles."""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime
from urllib.parse import urlencode

import httpx
import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit
from app.cache import client as redis_client
from app.db import get_session
from app.deps import Principal, current_user
from app.errors import ApiError
from app.models import Membership, Project, User
from app.schemas import AuthMethods, LoginRequest, TokenResponse, UserOut
from app.security import hash_password, issue_token, load_memberships, verify_password
from app.settings import settings

log = logging.getLogger("aperture.auth")
router = APIRouter(prefix="/auth", tags=["auth"])

OIDC_STATE_TTL = 600


@router.get("/methods", response_model=AuthMethods)
async def methods() -> AuthMethods:
    return AuthMethods(
        password=settings.allow_password_login,
        oidc=settings.oidc_enabled,
        ldap=settings.ldap_enabled,
        oidc_login_url="/v1/auth/oidc/start" if settings.oidc_enabled else None,
        instance_name=settings.instance_name,
    )


async def _finish_login(session: AsyncSession, user: User, request: Request,
                        response: Response) -> TokenResponse:
    user.last_login_at = datetime.now(UTC)
    roles = await load_memberships(session, user.id)
    token, ttl = issue_token(user, roles)
    await audit.record(
        session, action="user.login", project_id=None, actor_id=user.id, actor_label=user.email,
        target_type="user", target_id=user.id, target_label=user.email,
        payload={"provider": user.provider}, client_ip=_ip(request),
    )
    await session.commit()
    response.set_cookie(
        "aperture_session", token, max_age=ttl, httponly=True, samesite="lax",
        secure=settings.public_url.startswith("https"),
    )
    return TokenResponse(
        access_token=token, expires_in=ttl,
        user=UserOut(**{**_user_dict(user), "roles": roles}),
    )


def _user_dict(user: User) -> dict:
    return {
        "id": user.id, "email": user.email, "name": user.name, "provider": user.provider,
        "is_superadmin": user.is_superadmin, "is_active": user.is_active,
        "last_login_at": user.last_login_at,
    }


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request, response: Response,
                session: AsyncSession = Depends(get_session)) -> TokenResponse:
    email = body.email.strip().lower()
    user = (
        await session.execute(sa.select(User).where(sa.func.lower(User.email) == email))
    ).scalar_one_or_none()

    if user is not None and user.provider == "local":
        if not settings.allow_password_login:
            raise ApiError("forbidden", "password sign-in is disabled on this instance")
        if not verify_password(body.password, user.password_hash):
            await _record_failure(session, email, request)
            raise ApiError("invalid_credentials", "email or password is incorrect")
    elif settings.ldap_enabled:
        user = await _ldap_login(session, email, body.password)
    else:
        await _record_failure(session, email, request)
        raise ApiError("invalid_credentials", "email or password is incorrect")

    if not user.is_active:
        raise ApiError("forbidden", "this account is deactivated")
    return await _finish_login(session, user, request, response)


async def _record_failure(session: AsyncSession, email: str, request: Request) -> None:
    await audit.record(session, action="user.login_failed", project_id=None,
                       actor_label=email, target_type="user", target_label=email,
                       client_ip=_ip(request), commit=True)


async def _ldap_login(session: AsyncSession, email: str, password: str) -> User:
    try:
        from ldap3 import ALL, Connection, Server
    except ImportError as exc:
        raise ApiError("internal_error", "LDAP support is not installed") from exc

    username = email.split("@")[0]
    server = Server(settings.ldap_url, get_info=ALL)
    search_filter = settings.ldap_user_filter.format(username=username, email=email)
    try:
        binder = Connection(server, settings.ldap_bind_dn, settings.ldap_bind_password,
                            auto_bind=True)
        binder.search(settings.ldap_user_base_dn, search_filter,
                      attributes=["cn", "mail", "memberOf"])
        if not binder.entries:
            raise ApiError("invalid_credentials", "email or password is incorrect")
        entry = binder.entries[0]
        user_dn = entry.entry_dn
        groups = [str(g) for g in (entry.memberOf.values if "memberOf" in entry else [])]
        Connection(server, user_dn, password, auto_bind=True)  # the actual credential check
    except ApiError:
        raise
    except Exception as exc:
        log.warning("ldap bind failed", extra={"email": email}, exc_info=True)
        raise ApiError("invalid_credentials", "email or password is incorrect") from exc

    user = (
        await session.execute(sa.select(User).where(sa.func.lower(User.email) == email))
    ).scalar_one_or_none()
    if user is None:
        user = User(email=email, name=str(entry.cn) if "cn" in entry else email, provider="ldap",
                    is_superadmin=bool(settings.ldap_admin_group and
                                       settings.ldap_admin_group in groups))
        session.add(user)
        await session.flush()
        await _grant_default_membership(session, user)
    return user


async def _grant_default_membership(session: AsyncSession, user: User, role: str = "developer"
                                    ) -> None:
    """A newly federated user lands in every existing project as a developer.

    Small teams want this; larger installs turn it off by pre-seeding memberships
    and setting the role mapping in the identity provider instead.
    """
    projects = (await session.execute(sa.select(Project.id))).scalars().all()
    for project_id in projects:
        session.add(Membership(user_id=user.id, project_id=project_id, role=role))


# ------------------------------------------------------------------------ OIDC


@router.get("/oidc/start")
async def oidc_start(redirect_to: str = "/") -> dict:
    if not settings.oidc_enabled:
        raise ApiError("invalid_request", "OIDC is not configured on this instance")
    discovery = await _oidc_discovery()
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(16)
    await redis_client().set(f"aperture:oidc:{state}",
                             f"{nonce}|{redirect_to}", ex=OIDC_STATE_TTL)
    params = {
        "response_type": "code",
        "client_id": settings.oidc_client_id,
        "redirect_uri": f"{settings.public_url}/v1/auth/oidc/callback",
        "scope": " ".join(settings.oidc_scope_list),
        "state": state,
        "nonce": nonce,
    }
    return {"authorize_url": f"{discovery['authorization_endpoint']}?{urlencode(params)}"}


async def _oidc_discovery() -> dict:
    url = settings.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration"
    async with httpx.AsyncClient(timeout=10) as client:
        res = await client.get(url)
    if res.status_code >= 400:
        raise ApiError("upstream_error", f"OIDC discovery failed with HTTP {res.status_code}")
    return res.json()


@router.get("/oidc/callback", response_model=TokenResponse)
async def oidc_callback(code: str, state: str, request: Request, response: Response,
                        session: AsyncSession = Depends(get_session)) -> TokenResponse:
    if not settings.oidc_enabled:
        raise ApiError("invalid_request", "OIDC is not configured on this instance")
    stored = await redis_client().get(f"aperture:oidc:{state}")
    if not stored:
        raise ApiError("invalid_request", "this sign-in link expired, start again")
    await redis_client().delete(f"aperture:oidc:{state}")

    discovery = await _oidc_discovery()
    async with httpx.AsyncClient(timeout=15) as client:
        token_res = await client.post(discovery["token_endpoint"], data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": f"{settings.public_url}/v1/auth/oidc/callback",
            "client_id": settings.oidc_client_id,
            "client_secret": settings.oidc_client_secret,
        })
        if token_res.status_code >= 400:
            raise ApiError("invalid_credentials", "the identity provider rejected this sign-in")
        tokens = token_res.json()
        userinfo_res = await client.get(
            discovery["userinfo_endpoint"],
            headers={"authorization": f"Bearer {tokens['access_token']}"},
        )
    if userinfo_res.status_code >= 400:
        raise ApiError("upstream_error", "could not read the user profile from the provider")
    info = userinfo_res.json()

    email = (info.get("email") or "").lower()
    if not email:
        raise ApiError("invalid_credentials", "the provider did not return an email address")
    groups = info.get("groups") or info.get("roles") or []
    admin_groups = {g.strip() for g in settings.oidc_admin_groups.split(",") if g.strip()}
    dev_groups = {g.strip() for g in settings.oidc_developer_groups.split(",") if g.strip()}
    is_admin = bool(admin_groups & set(groups))

    user = (
        await session.execute(sa.select(User).where(sa.func.lower(User.email) == email))
    ).scalar_one_or_none()
    if user is None:
        user = User(email=email, name=info.get("name") or email, provider="oidc",
                    is_superadmin=is_admin)
        session.add(user)
        await session.flush()
        await _grant_default_membership(
            session, user, "developer" if (dev_groups & set(groups)) or is_admin else "viewer"
        )
    else:
        user.is_superadmin = user.is_superadmin or is_admin
    return await _finish_login(session, user, request, response)


# ------------------------------------------------------------------------ misc


@router.get("/me", response_model=UserOut)
async def me(principal: Principal = Depends(current_user),
             session: AsyncSession = Depends(get_session)) -> UserOut:
    user = await session.get(User, principal.id)
    if user is None:
        raise ApiError("unauthenticated", "this account no longer exists")
    return UserOut(**{**_user_dict(user), "roles": principal.roles})


@router.post("/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie("aperture_session")
    return {"ok": True}


@router.post("/password")
async def change_password(body: dict, principal: Principal = Depends(current_user),
                          session: AsyncSession = Depends(get_session)) -> dict:
    current = body.get("current_password") or ""
    new = body.get("new_password") or ""
    if len(new) < 10:
        raise ApiError("invalid_request", "the new password must be at least 10 characters")
    user = await session.get(User, principal.id)
    if user is None or user.provider != "local":
        raise ApiError("invalid_request", "this account does not use a password")
    if not verify_password(current, user.password_hash):
        raise ApiError("invalid_credentials", "the current password is incorrect")
    user.password_hash = hash_password(new)
    await session.commit()
    return {"ok": True}
