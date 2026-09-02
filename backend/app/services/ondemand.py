"""Bringing a version up because someone asked it a question.

The gateway's contract is deliberate: traffic only reaches replicas that were
deployed on purpose, so a client calling a model nobody deployed gets a clear
503 instead of triggering an unplanned 150 MB load on a shared runtime. That is
the right answer for traffic.

It is the wrong answer for "I just want to see what this version does". Making
someone visit a Deploy tab, choose a replica count and wait for a rollout before
they can look at one prediction is friction with nothing behind it - the version
is validated, the weights are in the object store, a runtime has capacity.

So callers can ask for it. This starts one replica for the version they named,
waits for it to report ready, and leaves it as an ordinary deployment: it shows
up in the fleet, it is stoppable, and it is in the audit log with on_demand set,
so nobody has to guess where it came from later.
"""

from __future__ import annotations

import asyncio
import logging

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import Principal
from app.errors import ApiError
from app.models import Assignment, Deployment, Model, ModelVersion, Project
from app import audit
from app.services.deployer import reconcile

log = logging.getLogger(__name__)

POLL_SECONDS = 0.75


async def ensure_serving(session: AsyncSession, project: Project, model: Model,
                         version: ModelVersion, principal: Principal,
                         *, timeout: float = 150.0,
                         client_ip: str | None = None) -> Deployment:
    """Make `version` answerable, starting a replica if none is running."""
    # No pre-flight against a sample image: loading it is the check, and that is
    # what starting a replica does. A version marked failed by an earlier load is
    # retried when the fleet has since gained a runtime that can read the format.
    artifact = version.artifact_info or {}
    if artifact.get("usability") in ("weights_only", "unknown"):
        raise ApiError(
            "model_not_deployed",
            (artifact.get("detail") or "this artifact carries no model graph")
            + " " + (artifact.get("next_step") or ""),
            details={"version": version.version, "artifact": artifact},
        )
    if version.status == "failed" and not await _fleet_can_load(session,
                                                                version.artifact_format):
        raise ApiError(
            "model_not_deployed",
            f"v{version.version} failed to load and no runtime in the fleet can read a "
            f"'{version.artifact_format}' artifact",
            details={"version": version.version},
        )

    deployment = (
        await session.execute(
            sa.select(Deployment).where(Deployment.version_id == version.id,
                                        Deployment.mode == "primary")
        )
    ).scalar_one_or_none()

    if deployment is None:
        deployment = Deployment(project_id=project.id, model_id=model.id,
                                version_id=version.id, mode="primary",
                                status="pending", replicas=1)
        session.add(deployment)
        created = True
    else:
        created = deployment.status == "stopped"
        if deployment.status == "stopped":
            deployment.status = "pending"
            deployment.stopped_at = None
        if deployment.replicas < 1:
            deployment.replicas = 1

    if created:
        await session.flush()
        await audit.record(
            session, action="deployment.create", project_id=project.id,
            actor_id=principal.id, actor_label=principal.label,
            target_type="deployment", target_id=deployment.id,
            target_label=f"{model.slug} v{version.version} (primary)",
            payload={"replicas": deployment.replicas, "on_demand": True},
            client_ip=client_ip,
        )
        log.info("starting a replica on demand",
                 extra={"model": model.slug, "version": version.version})

    await session.commit()
    await reconcile(session)

    if await _ready(session, deployment.id):
        await session.refresh(version)
        return deployment

    # The wait is the load: reading the weights out of the object store and
    # letting the backend warm up. Reconciling each round is what moves the
    # replica from pending to ready as the runtime reports in.
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(POLL_SECONDS)
        await reconcile(session)
        if await _ready(session, deployment.id):
            await session.refresh(deployment)
            await session.refresh(version)  # the load promoted it in another session
            return deployment

    await session.refresh(deployment)
    raise ApiError(
        "no_replica_available",
        f"started a replica for {model.slug} v{version.version} but it was not ready "
        f"within {int(timeout)}s"
        + (f" - {deployment.status_detail}" if deployment.status_detail else ""),
        details={"deployment": deployment.id, "status": deployment.status,
                 "detail": deployment.status_detail},
    )


async def _fleet_can_load(session: AsyncSession, fmt: str | None) -> bool:
    """Is a live runtime's image able to execute this artifact format?"""
    if not fmt:
        return True
    from app.services.deployer import _can_load, _fresh_runtimes

    return any(_can_load(runtime, fmt) for runtime in await _fresh_runtimes(session))

async def _ready(session: AsyncSession, deployment_id: str) -> bool:
    count = (
        await session.execute(
            sa.select(sa.func.count()).select_from(Assignment).where(
                Assignment.deployment_id == deployment_id, Assignment.state == "ready"
            )
        )
    ).scalar_one()
    return bool(count)
