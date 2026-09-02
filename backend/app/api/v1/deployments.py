"""R4.x - deployments, replicas, zero-downtime moves and one-click rollback."""

from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit
from app.cache import healthy_runtimes_for_version, invalidate_tag
from app.db import get_session
from app.deps import Principal, current_user, get_model_or_404, project_dep
from app.errors import ApiError
from app.models import Assignment, Deployment, Model, ModelVersion, Project, Runtime, Tag
from app.schemas import (
    DeploymentIn,
    DeploymentOut,
    DeploymentPatch,
    ReplicaOut,
    RollbackIn,
    RuntimeOut,
)
from app.serialize import dump, dump_loaded
from app.services.deployer import reconcile

router = APIRouter(tags=["deployments"])


async def deployment_out(session: AsyncSession, deployment: Deployment,
                         version_number: int | None = None) -> DeploymentOut:
    rows = (
        await session.execute(
            sa.select(Assignment, Runtime)
            .outerjoin(Runtime, Runtime.id == Assignment.runtime_id)
            .where(Assignment.deployment_id == deployment.id)
        )
    ).all()
    live = {r["runtime_id"]: r for r in await healthy_runtimes_for_version(deployment.version_id)}

    replicas = []
    for assignment, runtime in rows:
        beat = live.get(assignment.runtime_id, {})
        queues = beat.get("queues") or {}
        replicas.append(
            ReplicaOut(
                runtime_id=assignment.runtime_id,
                state=assignment.state,
                detail=assignment.detail,
                device=(runtime.device if runtime else beat.get("device", "")) or "",
                gpu_name=(runtime.gpu_name if runtime else "") or "",
                queue_depth=int(queues.get(deployment.version_id, 0)),
                ready_at=assignment.ready_at,
                url=(runtime.url if runtime else beat.get("url", "")) or "",
            )
        )
    if version_number is None:
        version = await session.get(ModelVersion, deployment.version_id)
        version_number = version.version if version else None

    return DeploymentOut.model_validate(
        await dump(session, deployment, version=version_number,
                   replicas_ready=sum(1 for r in replicas if r.state == "ready"),
                   replica_states=replicas)
    )


async def _version_by_number(session: AsyncSession, model: Model, number: int) -> ModelVersion:
    found = (
        await session.execute(
            sa.select(ModelVersion).where(ModelVersion.model_id == model.id,
                                          ModelVersion.version == number)
        )
    ).scalar_one_or_none()
    if found is None:
        raise ApiError("version_not_found", f"{model.slug} has no version {number}")
    return found


@router.get("/models/{ref}/deployments", response_model=list[DeploymentOut])
async def list_deployments(ref: str, include_stopped: bool = False,
                           project: Project = Depends(project_dep),
                           principal: Principal = Depends(current_user),
                           session: AsyncSession = Depends(get_session)) -> list[DeploymentOut]:
    principal.require(project.id, "viewer")
    model = await get_model_or_404(session, project.id, ref)
    stmt = (
        sa.select(Deployment, ModelVersion.version)
        .join(ModelVersion, ModelVersion.id == Deployment.version_id)
        .where(Deployment.model_id == model.id)
    )
    if not include_stopped:
        stmt = stmt.where(Deployment.status != "stopped")
    rows = (await session.execute(stmt.order_by(ModelVersion.version.desc()))).all()
    return [await deployment_out(session, d, v) for d, v in rows]


@router.post("/models/{ref}/deployments", response_model=DeploymentOut, status_code=201)
async def create_deployment(ref: str, body: DeploymentIn, request: Request,
                            project: Project = Depends(project_dep),
                            principal: Principal = Depends(current_user),
                            session: AsyncSession = Depends(get_session)) -> DeploymentOut:
    """R4.6 - a new deployment warms up on its own replicas; nothing else is touched
    until you move the tag, so traffic never lands on a cold model."""
    principal.require(project.id, "developer")
    model = await get_model_or_404(session, project.id, ref)

    if body.version_id:
        version = await session.get(ModelVersion, body.version_id)
        if version is None or version.model_id != model.id:
            raise ApiError("version_not_found", "that version does not belong to this model")
    elif body.version is not None:
        version = await _version_by_number(session, model, body.version)
    else:
        raise ApiError("invalid_request", "give a version number or a version id to deploy")

    # Deploying is the check: the replica has to load the graph, and that is the
    # only thing that can be established without a dataset. A version that cannot
    # load comes straight back as failed, with the runtime's own reason.
    artifact = version.artifact_info or {}
    if artifact.get("usability") in ("weights_only", "unknown"):
        raise ApiError(
            "conflict",
            (artifact.get("detail") or "this artifact carries no model graph")
            + " " + (artifact.get("next_step") or ""),
            details={"artifact": artifact},
        )
    if not version.weights_key:
        raise ApiError("upload_incomplete", "this version has no weights")

    existing = (
        await session.execute(
            sa.select(Deployment).where(Deployment.version_id == version.id,
                                        Deployment.mode == body.mode)
        )
    ).scalar_one_or_none()

    payload = body.model_dump(exclude={"version", "version_id"})
    if existing is not None:
        for key, value in payload.items():
            setattr(existing, key, value)
        if existing.status == "stopped":
            existing.status = "pending"
            existing.stopped_at = None
        deployment = existing
        action = "deployment.update"
    else:
        deployment = Deployment(project_id=project.id, model_id=model.id, version_id=version.id,
                               status="pending", **payload)
        session.add(deployment)
        action = "deployment.create"

    await session.flush()
    await audit.record(session, action=action, project_id=project.id, actor_id=principal.id,
                       actor_label=principal.label, target_type="deployment",
                       target_id=deployment.id,
                       target_label=f"{model.slug} v{version.version} ({body.mode})",
                       payload=payload,
                       client_ip=request.client.host if request.client else None)
    await session.commit()
    await reconcile(session)
    await session.refresh(deployment)
    return await deployment_out(session, deployment, version.version)


@router.patch("/deployments/{deployment_id}", response_model=DeploymentOut)
async def patch_deployment(deployment_id: str, body: DeploymentPatch, request: Request,
                           principal: Principal = Depends(current_user),
                           session: AsyncSession = Depends(get_session)) -> DeploymentOut:
    deployment = await session.get(Deployment, deployment_id)
    if deployment is None:
        raise ApiError("not_found", "no such deployment")
    principal.require(deployment.project_id, "developer")

    changes = body.model_dump(exclude_unset=True)
    if "replicas" in changes and changes["replicas"] == 0:
        deployment.status = "draining"
    for key, value in changes.items():
        setattr(deployment, key, value)
    if deployment.autoscale:
        deployment.replicas = max(deployment.min_replicas,
                                  min(deployment.replicas, deployment.max_replicas))

    action = "deployment.scale" if set(changes) <= {"replicas", "min_replicas", "max_replicas"} \
        else "deployment.update"
    await audit.record(session, action=action, project_id=deployment.project_id,
                       actor_id=principal.id, actor_label=principal.label,
                       target_type="deployment", target_id=deployment.id, payload=changes,
                       client_ip=request.client.host if request.client else None)
    await session.commit()
    await reconcile(session)
    await session.refresh(deployment)
    return await deployment_out(session, deployment)


@router.post("/deployments/{deployment_id}/stop", response_model=DeploymentOut)
async def stop_deployment(deployment_id: str, request: Request,
                          principal: Principal = Depends(current_user),
                          session: AsyncSession = Depends(get_session)) -> DeploymentOut:
    """Drain rather than kill: replicas finish in-flight work before unloading."""
    deployment = await session.get(Deployment, deployment_id)
    if deployment is None:
        raise ApiError("not_found", "no such deployment")
    principal.require(deployment.project_id, "developer")

    tags = list(
        (
            await session.execute(sa.select(Tag.name).where(Tag.version_id == deployment.version_id))
        ).scalars().all()
    )
    if tags and deployment.mode == "primary":
        raise ApiError(
            "conflict",
            f"this version is still tagged {', '.join(tags)} - move the tag to another "
            "deployed version first so callers keep working",
            details={"tags": tags},
        )

    deployment.status = "draining"
    deployment.replicas = 0
    await audit.record(session, action="deployment.stop", project_id=deployment.project_id,
                       actor_id=principal.id, actor_label=principal.label,
                       target_type="deployment", target_id=deployment.id,
                       client_ip=request.client.host if request.client else None)
    await session.commit()
    await reconcile(session)
    await session.refresh(deployment)
    return await deployment_out(session, deployment)


@router.post("/models/{ref}/rollback", response_model=DeploymentOut)
async def rollback(ref: str, body: RollbackIn, request: Request,
                   project: Project = Depends(project_dep),
                   principal: Principal = Depends(current_user),
                   session: AsyncSession = Depends(get_session)) -> DeploymentOut:
    """R2.3 - one click: repoint the tag at the previous version. No redeploy."""
    principal.require(project.id, "developer")
    model = await get_model_or_404(session, project.id, ref)
    tag = (
        await session.execute(
            sa.select(Tag).where(Tag.model_id == model.id, Tag.name == body.tag)
        )
    ).scalar_one_or_none()
    if tag is None:
        raise ApiError("tag_not_found", f"no tag '{body.tag}' on {model.slug}")
    if tag.locked:
        raise ApiError("tag_locked", f"tag '{body.tag}' is locked")

    if body.to_version is not None:
        target = await _version_by_number(session, model, body.to_version)
    elif tag.previous_version_id:
        found = await session.get(ModelVersion, tag.previous_version_id)
        if found is None:
            raise ApiError("version_not_found",
                           "the previous version no longer exists - name a version explicitly")
        target = found
    else:
        raise ApiError(
            "invalid_request",
            f"tag '{body.tag}' has never moved, so there is nothing to roll back to - "
            "name a version explicitly",
        )

    deployment = (
        await session.execute(
            sa.select(Deployment).where(Deployment.version_id == target.id,
                                        Deployment.mode == "primary")
        )
    ).scalar_one_or_none()
    if deployment is None or deployment.status == "stopped":
        raise ApiError(
            "conflict",
            f"version {target.version} is not deployed - rollback only repoints a tag, it "
            "does not start replicas. Deploy it first, then roll back.",
            details={"version": target.version},
        )

    previous = tag.version_id
    tag.previous_version_id = previous
    tag.version_id = target.id
    tag.updated_by = principal.id
    await session.flush()
    await invalidate_tag(model.id, tag.name)
    await audit.record(session, action="deployment.rollback", project_id=project.id,
                       actor_id=principal.id, actor_label=principal.label, target_type="tag",
                       target_id=tag.id, target_label=f"{model.slug}:{tag.name}",
                       payload={"to_version": target.version, "from_version_id": previous},
                       client_ip=request.client.host if request.client else None)
    await session.commit()
    return await deployment_out(session, deployment, target.version)


@router.get("/runtimes", response_model=list[RuntimeOut], tags=["fleet"])
async def list_runtimes(principal: Principal = Depends(current_user),
                        session: AsyncSession = Depends(get_session)) -> list[RuntimeOut]:
    from datetime import UTC, datetime, timedelta

    from app.settings import settings

    # freshest first: a fleet that has been rebuilt a few times accumulates rows
    # for runtimes that no longer exist, and burying the live ones is unhelpful
    rows = list(
        (
            await session.execute(
                sa.select(Runtime).order_by(Runtime.last_heartbeat.desc().nullslast(), Runtime.id)
            )
        ).scalars().all()
    )
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.runtime_heartbeat_ttl_s)
    return [
        RuntimeOut.model_validate(
            dump_loaded(r, fresh=bool(r.last_heartbeat and r.last_heartbeat >= cutoff))
        )
        for r in rows
    ]


@router.post("/fleet/reconcile", tags=["fleet"])
async def force_reconcile(principal: Principal = Depends(current_user),
                          session: AsyncSession = Depends(get_session)) -> dict:
    if not principal.is_superadmin and "admin" not in principal.roles.values():
        raise ApiError("forbidden", "reconciling the fleet needs an admin role")
    return await reconcile(session)
