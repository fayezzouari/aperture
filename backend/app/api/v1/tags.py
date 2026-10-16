"""R2.2/R2.3 - tags are the deploy interface. Moving one is the whole rollback story."""

from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit
from app.cache import invalidate_tag
from app.db import get_session
from app.deps import Principal, current_user, get_model_or_404
from app.errors import ApiError
from app.models import Model, ModelVersion, Tag
from app.schemas import TagIn, TagOut
from app.serialize import dump, dump_loaded

router = APIRouter(prefix="/models/{ref}/tags", tags=["tags"])

PROTECTED = {"production"}


async def _resolve_version(session: AsyncSession, model: Model, version: int | None,
                           version_id: str | None) -> ModelVersion:
    if version_id:
        found = await session.get(ModelVersion, version_id)
        if found is None or found.model_id != model.id:
            raise ApiError("version_not_found", "that version does not belong to this model")
    elif version is not None:
        found = (
            await session.execute(
                sa.select(ModelVersion).where(ModelVersion.model_id == model.id,
                                              ModelVersion.version == version)
            )
        ).scalar_one_or_none()
        if found is None:
            raise ApiError("version_not_found", f"{model.slug} has no version {version}")
    else:
        raise ApiError("invalid_request", "give either a version number or a version id")

    if found.status in ("draft", "validating", "failed"):
        raise ApiError(
            "conflict",
            f"version {found.version} is '{found.status}' - publish it before tagging it",
        )
    return found


@router.get("", response_model=list[TagOut])
async def list_tags(ref: str,
                    principal: Principal = Depends(current_user),
                    session: AsyncSession = Depends(get_session)) -> list[TagOut]:
    principal.require("viewer")
    model = await get_model_or_404(session, ref)
    rows = (
        await session.execute(
            sa.select(Tag, ModelVersion.version)
            .join(ModelVersion, ModelVersion.id == Tag.version_id)
            .where(Tag.model_id == model.id).order_by(Tag.name)
        )
    ).all()
    return [TagOut.model_validate(dump_loaded(t, version=v)) for t, v in rows]


@router.put("", response_model=TagOut)
async def set_tag(ref: str, body: TagIn, request: Request,
                  principal: Principal = Depends(current_user),
                  session: AsyncSession = Depends(get_session)) -> TagOut:
    """Create or move a tag. Atomic: one row update, then an explicit cache purge."""
    principal.require("developer")
    model = await get_model_or_404(session, ref)
    version = await _resolve_version(session, model, body.version, body.version_id)

    tag = (
        await session.execute(
            sa.select(Tag).where(Tag.model_id == model.id, Tag.name == body.name)
        )
    ).scalar_one_or_none()

    if tag is None:
        tag = Tag(model_id=model.id, name=body.name, version_id=version.id,
                  updated_by=principal.id)
        session.add(tag)
        action, previous = "tag.create", None
    else:
        if tag.locked:
            raise ApiError("tag_locked",
                           f"tag '{tag.name}' is locked - unlock it before moving it")
        if body.name in PROTECTED:
            principal.require("developer")
        previous = tag.version_id
        tag.previous_version_id = previous
        tag.version_id = version.id
        tag.updated_by = principal.id
        action = "tag.move"

    await session.flush()
    await invalidate_tag(model.id, body.name)
    await audit.record(
        session, action=action, actor_id=principal.id,
        actor_label=principal.label, target_type="tag", target_id=tag.id,
        target_label=f"{model.slug}:{body.name}",
        payload={"to_version": version.version, "from_version_id": previous},
        client_ip=request.client.host if request.client else None,
    )
    await session.commit()
    return TagOut.model_validate(await dump(session, tag, version=version.version))


@router.post("/{name}/lock", response_model=TagOut)
async def lock_tag(ref: str, name: str, request: Request, locked: bool = True,
                   principal: Principal = Depends(current_user),
                   session: AsyncSession = Depends(get_session)) -> TagOut:
    principal.require("admin")
    model = await get_model_or_404(session, ref)
    tag = (
        await session.execute(sa.select(Tag).where(Tag.model_id == model.id, Tag.name == name))
    ).scalar_one_or_none()
    if tag is None:
        raise ApiError("tag_not_found", f"no tag '{name}' on {model.slug}")
    tag.locked = locked
    version = await session.get(ModelVersion, tag.version_id)
    await audit.record(session, action="tag.lock", actor_id=principal.id,
                       actor_label=principal.label, target_type="tag", target_id=tag.id,
                       target_label=f"{model.slug}:{name}", payload={"locked": locked})
    await session.commit()
    return TagOut.model_validate(
        await dump(session, tag, version=version.version if version else None)
    )


@router.delete("/{name}", status_code=204)
async def delete_tag(ref: str, name: str, request: Request,
                     principal: Principal = Depends(current_user),
                     session: AsyncSession = Depends(get_session)) -> None:
    principal.require("developer")
    model = await get_model_or_404(session, ref)
    tag = (
        await session.execute(sa.select(Tag).where(Tag.model_id == model.id, Tag.name == name))
    ).scalar_one_or_none()
    if tag is None:
        raise ApiError("tag_not_found", f"no tag '{name}' on {model.slug}")
    if tag.locked:
        raise ApiError("tag_locked", f"tag '{name}' is locked")
    await session.delete(tag)
    await invalidate_tag(model.id, name)
    await audit.record(session, action="tag.delete", actor_id=principal.id,
                       actor_label=principal.label, target_type="tag", target_id=tag.id,
                       target_label=f"{model.slug}:{name}",
                       client_ip=request.client.host if request.client else None)
    await session.commit()
