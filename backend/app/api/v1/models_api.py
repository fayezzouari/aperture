"""R6.1 - the model catalog, and the CRUD behind it."""

from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit, storage
from app.db import get_session
from app.deps import Page, Principal, get_model_or_404, pagination, workspace_user
from app.errors import ApiError
from app.models import Assignment, Deployment, Model, ModelVersion, Tag
from app.schemas import ModelIn, ModelOut, ModelPatch, TagOut
from app.serialize import dump_loaded
from app.services.analytics import model_cards
from app.settings import settings

router = APIRouter(prefix="/models", tags=["models"])


async def _decorate(session: AsyncSession, models: list[Model]) -> list[ModelOut]:
    ids = [m.id for m in models]
    if not ids:
        return []

    version_rows = (
        await session.execute(
            sa.select(ModelVersion.model_id, sa.func.count(), sa.func.max(ModelVersion.version))
            .where(ModelVersion.model_id.in_(ids)).group_by(ModelVersion.model_id)
        )
    ).all()
    counts = {mid: (int(count), int(latest or 0)) for mid, count, latest in version_rows}

    tag_rows = (
        await session.execute(
            sa.select(Tag, ModelVersion.version)
            .join(ModelVersion, ModelVersion.id == Tag.version_id)
            .where(Tag.model_id.in_(ids)).order_by(Tag.name)
        )
    ).all()
    tags: dict[str, list[TagOut]] = {}
    for tag, number in tag_rows:
        tags.setdefault(tag.model_id, []).append(
            TagOut.model_validate(dump_loaded(tag, version=number))
        )

    deploy_rows = (
        await session.execute(
            sa.select(Deployment, ModelVersion.version)
            .join(ModelVersion, ModelVersion.id == Deployment.version_id)
            .where(Deployment.model_id.in_(ids), Deployment.status != "stopped")
        )
    ).all()
    ready_rows = dict(
        (
            await session.execute(
                sa.select(Assignment.deployment_id, sa.func.count())
                .where(Assignment.state == "ready").group_by(Assignment.deployment_id)
            )
        ).all()
    )
    deployed: dict[str, list[int]] = {}
    ready: dict[str, int] = {}
    desired: dict[str, int] = {}
    for deployment, number in deploy_rows:
        deployed.setdefault(deployment.model_id, []).append(number)
        ready[deployment.model_id] = ready.get(deployment.model_id, 0) + int(
            ready_rows.get(deployment.id, 0)
        )
        desired[deployment.model_id] = desired.get(deployment.model_id, 0) + deployment.replicas

    traffic = await model_cards(session, model_ids=ids)

    out = []
    for model in models:
        count, latest = counts.get(model.id, (0, 0))
        stats = traffic.get(model.id, {})
        out.append(
            ModelOut(
                **{c.name: getattr(model, c.name) for c in Model.__table__.columns
                   if c.name in ModelOut.model_fields},
                version_count=count,
                latest_version=latest or None,
                tags=tags.get(model.id, []),
                deployed_versions=sorted(set(deployed.get(model.id, []))),
                replicas_ready=ready.get(model.id, 0),
                replicas_desired=desired.get(model.id, 0),
                requests_24h=stats.get("requests_24h", 0),
                error_rate_24h=stats.get("error_rate_24h", 0.0),
                p95_ms_24h=stats.get("p95_ms_24h"),
            )
        )
    return out


@router.get("", response_model=list[ModelOut])
async def list_models(
    q: str | None = Query(None, description="matches slug, name, owner or description"),
    task: str | None = None,
    owner: str | None = None,
    label: str | None = None,
    tag: str | None = Query(None, description="only models carrying this tag"),
    deployed: bool | None = Query(None, description="only models with a live deployment"),
    include_archived: bool = False,
    page: Page = Depends(pagination),
    principal: Principal = Depends(workspace_user),
    session: AsyncSession = Depends(get_session),
) -> list[ModelOut]:
    principal.require("viewer")
    stmt = sa.select(Model).where(Model.workspace_id == principal.workspace_id)
    if not include_archived:
        stmt = stmt.where(Model.archived_at.is_(None))
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            sa.or_(sa.func.lower(Model.slug).like(like), sa.func.lower(Model.name).like(like),
                   sa.func.lower(Model.owner).like(like),
                   sa.func.lower(Model.description).like(like))
        )
    if task:
        stmt = stmt.where(Model.task == task)
    if owner:
        stmt = stmt.where(sa.func.lower(Model.owner) == owner.lower())
    if tag:
        stmt = stmt.where(Model.id.in_(sa.select(Tag.model_id).where(Tag.name == tag)))
    if deployed:
        stmt = stmt.where(
            Model.id.in_(sa.select(Deployment.model_id).where(Deployment.status == "live"))
        )
    stmt = stmt.order_by(Model.updated_at.desc()).limit(page.limit).offset(page.offset)
    models = list((await session.execute(stmt)).scalars().all())
    if label:
        models = [m for m in models if label in (m.labels or [])]
    return await _decorate(session, models)


@router.post("", response_model=ModelOut, status_code=201)
async def create_model(body: ModelIn, request: Request,
                       principal: Principal = Depends(workspace_user),
                       session: AsyncSession = Depends(get_session)) -> ModelOut:
    principal.require("developer")
    existing = (
        await session.execute(
            sa.select(Model).where(Model.workspace_id == principal.workspace_id,
                                   Model.slug == body.slug)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ApiError("slug_taken", f"'{body.slug}' already exists in this workspace")

    model = Model(workspace_id=principal.workspace_id, **body.model_dump())
    session.add(model)
    await session.flush()
    await audit.record(session, action="model.create", actor_id=principal.id,
                       actor_label=principal.label, target_type="model",
                       target_id=model.id, target_label=model.slug, payload=body.model_dump(),
                       client_ip=request.client.host if request.client else None)
    await session.commit()
    return (await _decorate(session, [model]))[0]


@router.get("/{ref}", response_model=ModelOut)
async def get_model(ref: str,
                    principal: Principal = Depends(workspace_user),
                    session: AsyncSession = Depends(get_session)) -> ModelOut:
    principal.require("viewer")
    model = await get_model_or_404(session, ref, principal.workspace_id)
    return (await _decorate(session, [model]))[0]


@router.patch("/{ref}", response_model=ModelOut)
async def update_model(ref: str, body: ModelPatch, request: Request,
                       principal: Principal = Depends(workspace_user),
                       session: AsyncSession = Depends(get_session)) -> ModelOut:
    principal.require("developer")
    model = await get_model_or_404(session, ref, principal.workspace_id)
    changes = body.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(model, key, value)
    await audit.record(session, action="model.update", actor_id=principal.id,
                       actor_label=principal.label, target_type="model",
                       target_id=model.id, target_label=model.slug, payload=changes,
                       client_ip=request.client.host if request.client else None)
    await session.commit()
    return (await _decorate(session, [model]))[0]


@router.post("/{ref}/archive", response_model=ModelOut)
async def archive_model(ref: str, request: Request,
                        principal: Principal = Depends(workspace_user),
                        session: AsyncSession = Depends(get_session)) -> ModelOut:
    principal.require("admin")
    model = await get_model_or_404(session, ref, principal.workspace_id)
    live = (
        await session.execute(
            sa.select(sa.func.count()).select_from(Deployment)
            .where(Deployment.model_id == model.id, Deployment.status.in_(("live", "warming")))
        )
    ).scalar_one()
    if live:
        raise ApiError("conflict",
                       "stop the live deployments before archiving this model")
    model.archived_at = sa.func.now()
    await audit.record(session, action="model.archive", actor_id=principal.id,
                       actor_label=principal.label, target_type="model",
                       target_id=model.id, target_label=model.slug,
                       client_ip=request.client.host if request.client else None)
    await session.commit()
    return (await _decorate(session, [model]))[0]


@router.delete("/{ref}", status_code=204)
async def delete_model(ref: str, request: Request, purge_weights: bool = True,
                       principal: Principal = Depends(workspace_user),
                       session: AsyncSession = Depends(get_session)) -> None:
    """Removes the model, its versions and (by default) its weights from storage."""
    principal.require("admin")
    model = await get_model_or_404(session, ref, principal.workspace_id)
    live = (
        await session.execute(
            sa.select(sa.func.count()).select_from(Deployment)
            .where(Deployment.model_id == model.id, Deployment.status != "stopped")
        )
    ).scalar_one()
    if live:
        raise ApiError("conflict", "stop every deployment of this model before deleting it")

    if purge_weights:
        for prefix in await _storage_prefixes(session, model):
            await storage.delete_prefix(settings.s3_bucket_weights, prefix)
            await storage.delete_prefix(settings.s3_bucket_captures, prefix)

    await audit.record(session, action="model.delete", actor_id=principal.id,
                       actor_label=principal.label, target_type="model",
                       target_id=model.id, target_label=model.slug,
                       payload={"purge_weights": purge_weights},
                       client_ip=request.client.host if request.client else None)
    await session.delete(model)
    await session.commit()


async def _storage_prefixes(session: AsyncSession, model: Model) -> set[str]:
    """Every object-store directory this model's weights and captures can live under.

    New objects sit under `{workspace id}/{model}/`. Older installs wrote
    `{model}/` (one instance, no workspaces) or `{project}/{model}/` (before
    that); those keys are still on the version rows, so their directories are
    found from there rather than guessed. A bare `{model}/` is cleared whole only
    when no other model's weights sit under it - a project of the same name once
    did - and otherwise version by version.
    """
    prefixes = {f"{model.storage_prefix}/"}
    keys = (
        await session.execute(
            sa.select(ModelVersion.weights_key).where(ModelVersion.model_id == model.id,
                                                      ModelVersion.weights_key.is_not(None))
        )
    ).scalars().all()
    for key in keys:
        parts = key.split("/")
        if len(parts) >= 3 and parts[-3] == model.slug:
            prefixes.add("/".join(parts[:-2]) + "/")
    bare = f"{model.slug}/"
    if bare in prefixes:
        shared = (
            await session.execute(
                sa.select(ModelVersion.id).where(ModelVersion.model_id != model.id,
                                                 ModelVersion.weights_key.like(f"{bare}%"))
                .limit(1)
            )
        ).first()
        if shared is not None:
            prefixes.discard(bare)
            prefixes |= {key.rsplit("/", 1)[0] + "/" for key in keys
                         if key.count("/") == 2 and key.startswith(bare)}
    return prefixes
