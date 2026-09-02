"""Turning `{model}/{version|tag}` into a concrete, servable version.

R2.2/R2.3: clients call a tag, we resolve it per request. A retag is therefore
visible on the very next call with no redeploy - the resolution cache lives for
a few seconds and is invalidated explicitly when a tag moves.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from aperture_core.configspec import ModelConfig
from app.cache import cache_tag, read_tag
from app.errors import ApiError
from app.models import Deployment, Model, ModelVersion, Tag

log = logging.getLogger("aperture.resolver")


@dataclass(slots=True)
class Resolved:
    model: Model
    version: ModelVersion
    tag: str | None
    config: ModelConfig


async def resolve_version(session: AsyncSession, model: Model, ref: str) -> tuple[ModelVersion, str | None]:
    """`ref` is a version number ("7", "v7"), a version id, or a tag name."""
    raw = (ref or "").strip()
    if not raw:
        raise ApiError("invalid_request", "a version number or tag is required")

    number = raw[1:] if raw[:1].lower() == "v" and raw[1:].isdigit() else raw
    if number.isdigit():
        found = (
            await session.execute(
                sa.select(ModelVersion).where(
                    ModelVersion.model_id == model.id, ModelVersion.version == int(number)
                )
            )
        ).scalar_one_or_none()
        if found is None:
            raise ApiError("version_not_found", f"{model.slug} has no version {number}")
        return found, None

    if len(raw) == 36 and raw.count("-") == 4:
        found = await session.get(ModelVersion, raw)
        if found is not None and found.model_id == model.id:
            return found, None

    cached = await read_tag(model.id, raw)
    if cached:
        found = await session.get(ModelVersion, cached["version_id"])
        if found is not None:
            return found, raw

    tag = (
        await session.execute(
            sa.select(Tag).where(Tag.model_id == model.id, Tag.name == raw)
        )
    ).scalar_one_or_none()
    if tag is None:
        raise ApiError(
            "tag_not_found",
            f"'{raw}' is neither a version number nor a tag on {model.slug}",
        )
    version = await session.get(ModelVersion, tag.version_id)
    if version is None:
        raise ApiError("version_not_found", f"tag '{raw}' points at a version that no longer exists")
    await cache_tag(model.id, raw, {"version_id": version.id, "version": version.version})
    return version, raw


async def resolve(session: AsyncSession, model: Model, ref: str) -> Resolved:
    version, tag = await resolve_version(session, model, ref)
    try:
        config = ModelConfig.model_validate(version.config or {})
    except Exception as exc:
        raise ApiError("invalid_config", f"version {version.version} has an invalid config: {exc}")
    return Resolved(model=model, version=version, tag=tag, config=config)


async def live_deployments(session: AsyncSession, model_id: str) -> list[Deployment]:
    rows = (
        await session.execute(
            sa.select(Deployment).where(
                Deployment.model_id == model_id,
                Deployment.status.in_(("warming", "live", "draining")),
            )
        )
    ).scalars().all()
    return list(rows)


async def deployment_for_version(session: AsyncSession, version_id: str,
                                 mode: str = "primary") -> Deployment | None:
    return (
        await session.execute(
            sa.select(Deployment).where(
                Deployment.version_id == version_id, Deployment.mode == mode
            )
        )
    ).scalar_one_or_none()
