"""R7.2 - who can use this instance, and with which role. Admin only."""

from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit
from app.db import get_session
from app.deps import Principal, current_user
from app.errors import ApiError
from app.models import User
from app.schemas import UserInviteIn, UserOut, UserPatch
from app.serialize import dump

router = APIRouter(prefix="/users", tags=["users"])


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def _out(session: AsyncSession, user: User) -> UserOut:
    return UserOut.model_validate(await dump(session, user))


@router.get("", response_model=list[UserOut])
async def list_users(principal: Principal = Depends(current_user),
                     session: AsyncSession = Depends(get_session)) -> list[UserOut]:
    principal.require("admin")
    users = (await session.execute(sa.select(User).order_by(User.email))).scalars().all()
    return [UserOut.model_validate(user) for user in users]


@router.put("", response_model=UserOut)
async def invite_user(body: UserInviteIn, request: Request,
                      principal: Principal = Depends(current_user),
                      session: AsyncSession = Depends(get_session)) -> UserOut:
    """Invite someone, or set the role of someone who already has an account.

    An unknown email gets a placeholder account without a password: they sign in
    through OIDC or LDAP, or an admin sets a password for them.
    """
    principal.require("admin")
    email = body.email.strip().lower()
    if "@" not in email:
        raise ApiError("invalid_request", "give an email address")
    user = (
        await session.execute(sa.select(User).where(sa.func.lower(User.email) == email))
    ).scalar_one_or_none()
    if user is None:
        user = User(email=email, name=email, provider="local", password_hash=None,
                    role=body.role)
        session.add(user)
        await session.flush()
        action = "user.invite"
    else:
        _guard_self(principal, user, role=body.role)
        user.role = body.role
        action = "user.role_change"
    await audit.record(session, action=action, actor_id=principal.id,
                       actor_label=principal.label, target_type="user", target_id=user.id,
                       target_label=user.email, payload={"role": body.role},
                       client_ip=_ip(request))
    await session.commit()
    return await _out(session, user)


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(user_id: str, body: UserPatch, request: Request,
                      principal: Principal = Depends(current_user),
                      session: AsyncSession = Depends(get_session)) -> UserOut:
    principal.require("admin")
    user = await _user(session, user_id)
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    _guard_self(principal, user, role=changes.get("role"), active=changes.get("is_active"))
    for key, value in changes.items():
        setattr(user, key, value)
    if "role" in changes:
        await audit.record(session, action="user.role_change", actor_id=principal.id,
                           actor_label=principal.label, target_type="user", target_id=user.id,
                           target_label=user.email, payload={"role": changes["role"]},
                           client_ip=_ip(request))
    if changes.get("is_active") is False:
        await audit.record(session, action="user.deactivate", actor_id=principal.id,
                           actor_label=principal.label, target_type="user", target_id=user.id,
                           target_label=user.email, client_ip=_ip(request))
    await session.commit()
    return await _out(session, user)


@router.delete("/{user_id}", status_code=204)
async def deactivate_user(user_id: str, request: Request,
                          principal: Principal = Depends(current_user),
                          session: AsyncSession = Depends(get_session)) -> None:
    """Deactivate rather than delete: the audit trail keeps pointing at a real row."""
    principal.require("admin")
    user = await _user(session, user_id)
    _guard_self(principal, user, active=False)
    user.is_active = False
    await audit.record(session, action="user.deactivate", actor_id=principal.id,
                       actor_label=principal.label, target_type="user", target_id=user.id,
                       target_label=user.email, client_ip=_ip(request))
    await session.commit()


async def _user(session: AsyncSession, user_id: str) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise ApiError("not_found", "no such user")
    return user


def _guard_self(principal: Principal, user: User, *, role: str | None = None,
                active: bool | None = None) -> None:
    """An admin cannot lock themselves out; another admin has to do it."""
    if user.id != principal.id:
        return
    if role is not None and role != "admin":
        raise ApiError("conflict", "you cannot take the admin role away from yourself")
    if active is False:
        raise ApiError("conflict", "you cannot deactivate your own account")
