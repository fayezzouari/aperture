"""R7.1 / R4.7 - API keys: one workspace each, scoped to models, rate limited, revocable."""

from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit
from app.cache import quota_usage
from app.db import get_session
from app.deps import Principal, workspace_user
from app.errors import ApiError
from app.models import ApiKey, Model
from app.schemas import ApiKeyCreated, ApiKeyIn, ApiKeyOut, ApiKeyPatch
from app.security import expires_at, generate_api_key
from app.serialize import dump
from app.settings import settings

router = APIRouter(prefix="/keys", tags=["api keys"])


async def _out(session: AsyncSession, key: ApiKey) -> ApiKeyOut:
    return ApiKeyOut.model_validate(
        await dump(session, key, used_this_month=await quota_usage(key.id))
    )


async def _key(session: AsyncSession, key_id: str, principal: Principal) -> ApiKey:
    key = (
        await session.execute(
            sa.select(ApiKey).where(ApiKey.id == key_id,
                                    ApiKey.workspace_id == principal.workspace_id)
        )
    ).scalar_one_or_none()
    if key is None:
        raise ApiError("not_found", "no such API key")
    return key


@router.get("", response_model=list[ApiKeyOut])
async def list_keys(include_revoked: bool = False,
                    principal: Principal = Depends(workspace_user),
                    session: AsyncSession = Depends(get_session)) -> list[ApiKeyOut]:
    principal.require("developer")
    stmt = sa.select(ApiKey).where(ApiKey.workspace_id == principal.workspace_id)
    if not include_revoked:
        stmt = stmt.where(ApiKey.revoked_at.is_(None))
    keys = list((await session.execute(stmt.order_by(ApiKey.created_at.desc()))).scalars().all())
    return [await _out(session, k) for k in keys]


@router.post("", response_model=ApiKeyCreated, status_code=201)
async def create_key(body: ApiKeyIn, request: Request,
                     principal: Principal = Depends(workspace_user),
                     session: AsyncSession = Depends(get_session)) -> ApiKeyCreated:
    principal.require("admin")

    scopes = body.scopes or ["*"]
    if "*" not in scopes:
        known = set(
            (
                await session.execute(
                    sa.select(Model.slug).where(Model.workspace_id == principal.workspace_id)
                )
            ).scalars().all()
        ) | set(
            (
                await session.execute(
                    sa.select(Model.id).where(Model.workspace_id == principal.workspace_id)
                )
            ).scalars().all()
        )
        unknown = [s for s in scopes if s not in known]
        if unknown:
            raise ApiError("invalid_request",
                           f"these scopes name no model in this workspace: {', '.join(unknown)}")

    full, prefix, key_hash = generate_api_key()
    key = ApiKey(
        workspace_id=principal.workspace_id, name=body.name, prefix=prefix, key_hash=key_hash, scopes=scopes,
        rate_limit_rpm=body.rate_limit_rpm or settings.default_rate_limit_rpm,
        monthly_quota=body.monthly_quota, created_by=principal.id,
        expires_at=expires_at(body.expires_in_days),
    )
    session.add(key)
    await session.flush()
    await audit.record(session, action="apikey.create", actor_id=principal.id,
                       actor_label=principal.label, target_type="apikey",
                       target_id=key.id, target_label=key.name,
                       payload={"scopes": scopes, "rate_limit_rpm": key.rate_limit_rpm,
                                "prefix": prefix},
                       client_ip=request.client.host if request.client else None)
    await session.commit()
    return ApiKeyCreated.model_validate(await dump(session, key, used_this_month=0, key=full))


@router.patch("/{key_id}", response_model=ApiKeyOut)
async def patch_key(key_id: str, body: ApiKeyPatch, request: Request,
                    principal: Principal = Depends(workspace_user),
                    session: AsyncSession = Depends(get_session)) -> ApiKeyOut:
    key = await _key(session, key_id, principal)
    principal.require("admin")
    changes = body.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(key, field, value)
    await audit.record(session, action="apikey.update", actor_id=principal.id,
                       actor_label=principal.label, target_type="apikey",
                       target_id=key.id, target_label=key.name, payload=changes,
                       client_ip=request.client.host if request.client else None)
    await session.commit()
    return await _out(session, key)


@router.post("/{key_id}/revoke", response_model=ApiKeyOut)
async def revoke_key(key_id: str, request: Request,
                     principal: Principal = Depends(workspace_user),
                     session: AsyncSession = Depends(get_session)) -> ApiKeyOut:
    key = await _key(session, key_id, principal)
    principal.require("admin")
    key.revoked_at = sa.func.now()
    await audit.record(session, action="apikey.revoke", actor_id=principal.id,
                       actor_label=principal.label, target_type="apikey",
                       target_id=key.id, target_label=key.name,
                       client_ip=request.client.host if request.client else None)
    await session.commit()
    await session.refresh(key)
    return await _out(session, key)
