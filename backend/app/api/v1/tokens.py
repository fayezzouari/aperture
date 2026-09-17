"""R8.1 - personal access tokens: a user's own long-lived credential for agents and scripts.

A token is the user. It inherits the owner's project roles at request time, is
stored only as a hash, is shown exactly once, and can be revoked by its owner.
Everything here is self-service: no project, no role check beyond "signed in".
"""

from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit
from app.db import get_session
from app.deps import Principal, current_user
from app.errors import ApiError
from app.models import PersonalAccessToken
from app.schemas import TokenCreated, TokenIn, TokenOut
from app.security import expires_at, generate_pat
from app.serialize import dump

router = APIRouter(prefix="/auth/tokens", tags=["auth"])


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.get("", response_model=list[TokenOut])
async def list_tokens(include_revoked: bool = False,
                      principal: Principal = Depends(current_user),
                      session: AsyncSession = Depends(get_session)) -> list[TokenOut]:
    stmt = sa.select(PersonalAccessToken).where(PersonalAccessToken.user_id == principal.id)
    if not include_revoked:
        stmt = stmt.where(PersonalAccessToken.revoked_at.is_(None))
    rows = (await session.execute(stmt.order_by(PersonalAccessToken.created_at.desc()))).scalars()
    return [TokenOut.model_validate(await dump(session, row)) for row in rows]


@router.post("", response_model=TokenCreated, status_code=201)
async def create_token(body: TokenIn, request: Request,
                       principal: Principal = Depends(current_user),
                       session: AsyncSession = Depends(get_session)) -> TokenCreated:
    if principal.token is not None:
        # a leaked token must not be able to mint successors that outlive its revocation
        raise ApiError("forbidden", "sign in with a browser session to create tokens")

    full, prefix, token_hash = generate_pat()
    token = PersonalAccessToken(
        user_id=principal.id, name=body.name, prefix=prefix, token_hash=token_hash,
        expires_at=expires_at(body.expires_in_days),
    )
    session.add(token)
    await session.flush()
    await audit.record(session, action="token.create", project_id=None,
                       actor_id=principal.id, actor_label=principal.label, target_type="token",
                       target_id=token.id, target_label=token.name,
                       payload={"prefix": prefix,
                                "expires_at": token.expires_at.isoformat()
                                if token.expires_at else None},
                       client_ip=_ip(request))
    await session.commit()
    return TokenCreated.model_validate(await dump(session, token, token=full))


@router.post("/{token_id}/revoke", response_model=TokenOut)
async def revoke_token(token_id: str, request: Request,
                       principal: Principal = Depends(current_user),
                       session: AsyncSession = Depends(get_session)) -> TokenOut:
    token = await session.get(PersonalAccessToken, token_id)
    if token is None or token.user_id != principal.id:
        # same answer for "not yours" and "does not exist": nothing to enumerate
        raise ApiError("not_found", "no such token")
    if token.revoked_at is None:
        token.revoked_at = sa.func.now()
        await audit.record(session, action="token.revoke", project_id=None,
                           actor_id=principal.id, actor_label=principal.label,
                           target_type="token", target_id=token.id, target_label=token.name,
                           payload={"prefix": token.prefix}, client_ip=_ip(request))
        await session.commit()
        await session.refresh(token)
    return TokenOut.model_validate(await dump(session, token))
