"""The reconciler: desired deployments -> per-runtime assignments.

This is the piece a Kubernetes operator would own in a large install. Here it is
a small bin-packer that runs on every heartbeat and on every deployment change:

  * spread a version's replicas across distinct runtimes (never two on one)
  * honour an explicit device request (R4.1); "auto" prefers a GPU runtime
  * bring a new version to `ready` *before* the old one starts draining (R4.6)
  * pull replicas that stopped heartbeating and re-place them elsewhere (R4.5)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import healthy_runtimes_for_version
from app.metrics import replicas_desired, replicas_ready
from app.models import Assignment, Deployment, Model, ModelVersion, Runtime
from app.settings import settings

log = logging.getLogger("aperture.deployer")

ACTIVE_STATES = ("pending", "loading", "warming", "ready")


# An ONNX-only image says nothing about torch, so a runtime that predates
# capability reporting is assumed to do what every image has always done.
_ASSUMED_FORMATS = ("onnx", "ort")

_FORMAT_ALIASES = {
    "pt": ("pt", "torchscript", "ultralytics"),
    "pth": ("pt", "torchscript", "ultralytics"),
    "torchscript": ("torchscript", "pt"),
    "onnx": ("onnx", "ort"),
    "engine": ("tensorrt", "engine", "plan"),
    "plan": ("tensorrt", "engine", "plan"),
}


def _requirement(fmt: str | None, artifact: dict | None) -> tuple[str, ...]:
    """What a runtime must advertise to load this particular artifact.

    Format alone is too coarse for a `.pt`: torch is enough for a TorchScript
    archive or an Ultralytics checkpoint, while an RF-DETR state_dict has no
    graph and needs the `rfdetr` package to be rebuilt from its layer shapes.
    Placing one on a torch-only replica would fail at load, one replica at a
    time, with nothing saying why.
    """
    info = artifact or {}
    if info.get("kind") == "state_dict" and info.get("family") == "rf-detr":
        return ("rfdetr",)
    if not fmt:
        return ()
    return _FORMAT_ALIASES.get(fmt.lower(), (fmt.lower(),))


def _can_load(runtime, fmt: str | None, artifact: dict | None = None) -> bool:
    """Can this runtime's image actually execute that artifact?"""
    wanted = _requirement(fmt, artifact)
    if not wanted:
        return True
    advertised = tuple((runtime.metrics or {}).get("formats") or _ASSUMED_FORMATS)
    return any(name in advertised for name in wanted)


def _device_fits(deployment: Deployment, runtime: Runtime) -> bool:
    want = (deployment.device or "auto").lower()
    if want == "auto":
        return True
    if want == "cpu":
        return runtime.device == "cpu"
    return runtime.device.startswith("cuda") and (
        want == "cuda" or runtime.device == want or want in (runtime.gpu_name or "").lower()
    )


async def _fresh_runtimes(session: AsyncSession) -> list[Runtime]:
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.runtime_heartbeat_ttl_s)
    rows = (
        await session.execute(
            sa.select(Runtime).where(
                Runtime.last_heartbeat.is_not(None), Runtime.last_heartbeat >= cutoff
            )
        )
    ).scalars().all()
    return list(rows)


async def reconcile(session: AsyncSession) -> dict:
    """Idempotent. Safe to call from the heartbeat path and from a cron tick."""
    runtimes = await _fresh_runtimes(session)
    runtime_ids = {r.id for r in runtimes}

    deployments = list(
        (
            await session.execute(
                sa.select(Deployment).where(Deployment.status != "stopped")
            )
        ).scalars().all()
    )
    assignments = list((await session.execute(sa.select(Assignment))).scalars().all())

    by_deployment: dict[str, list[Assignment]] = {}
    for a in assignments:
        by_deployment.setdefault(a.deployment_id, []).append(a)

    # A replica whose runtime went away is not a replica. Drop it so it gets re-placed.
    orphaned = 0
    for a in assignments:
        if a.runtime_id not in runtime_ids:
            await session.delete(a)
            orphaned += 1
    if orphaned:
        log.warning("dropped assignments on dead runtimes", extra={"count": orphaned})

    load: dict[str, int] = {r.id: 0 for r in runtimes}
    for a in assignments:
        if a.runtime_id in load and a.state in ACTIVE_STATES:
            load[a.runtime_id] += 1

    version_artifacts = {
        version_id: (fmt, info)
        for version_id, fmt, info in (
            await session.execute(
                sa.select(ModelVersion.id, ModelVersion.artifact_format,
                          ModelVersion.artifact_info).where(
                    ModelVersion.id.in_([d.version_id for d in deployments])
                )
            )
        ).all()
    } if deployments else {}

    created = removed = 0
    for deployment in deployments:
        current = [
            a for a in by_deployment.get(deployment.id, [])
            if a.runtime_id in runtime_ids and a.state != "failed"
        ]
        want = 0 if deployment.status in ("draining", "stopped") else deployment.replicas

        if len(current) > want:
            # drain the newest replicas first so a warm one keeps serving
            for a in sorted(current, key=lambda x: x.created_at, reverse=True)[: len(current) - want]:
                await session.delete(a)
                load[a.runtime_id] = max(0, load.get(a.runtime_id, 1) - 1)
                removed += 1
            current = current[: want]

        if len(current) < want:
            taken = {a.runtime_id for a in current}
            fmt, artifact = version_artifacts.get(deployment.version_id, (None, None))
            candidates = [
                r for r in runtimes
                if r.id not in taken and _device_fits(deployment, r) and r.status == "ready"
                and _can_load(r, fmt, artifact)
            ]
            if (not candidates and _requirement(fmt, artifact)
                    and not any(_can_load(r, fmt, artifact) for r in runtimes)):
                # Say which capability is missing. "waiting for a runtime with
                # capacity" sends people looking for the wrong problem.
                needs = " or ".join(_requirement(fmt, artifact))
                deployment.status_detail = (
                    f"no runtime advertises '{needs}', so a '{fmt}' artifact cannot be loaded - "
                    f"start one that can (docker compose --profile torch up -d runtime-torch), "
                    f"or convert the weights"
                )
            # least-loaded first; GPU runtimes win ties for an "auto" deployment
            candidates.sort(
                key=lambda r: (load.get(r.id, 0), 0 if r.device.startswith("cuda") else 1, r.id)
            )
            for runtime in candidates[: want - len(current)]:
                session.add(
                    Assignment(
                        deployment_id=deployment.id,
                        runtime_id=runtime.id,
                        version_id=deployment.version_id,
                        state="pending",
                    )
                )
                load[runtime.id] = load.get(runtime.id, 0) + 1
                created += 1

    await session.commit()
    await refresh_deployment_status(session)
    await _heal_stale_checks(session)
    return {
        "runtimes": len(runtimes),
        "deployments": len(deployments),
        "assignments_created": created,
        "assignments_removed": removed,
        "assignments_orphaned": orphaned,
    }


async def _heal_stale_checks(session: AsyncSession) -> None:
    """Replace verdicts that predate the load check.

    A version already serving carries whatever the old image-based validation
    wrote - including warnings about a synthetic sample. Nothing will transition
    its replica again, so the record would sit there forever; a version that is
    loaded right now is checked by definition, so it is rewritten to say that.
    """
    rows = (
        await session.execute(
            sa.select(ModelVersion, Assignment.runtime_id)
            .join(Assignment, Assignment.version_id == ModelVersion.id)
            .where(Assignment.state == "ready")
        )
    ).all()
    changed = False
    for version, runtime_id in rows:
        record = version.validation or {}
        if record.get("source") == "runtime-load":
            continue
        version.validation = {
            "status": "passed",
            "source": "runtime-load",
            "checked_at": datetime.now(UTC).isoformat(),
            "runtime_id": runtime_id,
            "errors": [],
            "warnings": [],
        }
        if version.status in ("draft", "validating", "failed"):
            version.status = "ready"
        changed = True
    if changed:
        await session.commit()

async def refresh_deployment_status(session: AsyncSession) -> None:
    """Roll assignment states up into the deployment status the UI shows."""
    deployments = list(
        (await session.execute(sa.select(Deployment).where(Deployment.status != "stopped")))
        .scalars().all()
    )
    if not deployments:
        return
    rows = (
        await session.execute(
            sa.select(Assignment.deployment_id, Assignment.state, sa.func.count())
            .where(Assignment.deployment_id.in_([d.id for d in deployments]))
            .group_by(Assignment.deployment_id, Assignment.state)
        )
    ).all()
    counts: dict[str, dict[str, int]] = {}
    for deployment_id, state, count in rows:
        counts.setdefault(deployment_id, {})[state] = count

    version_numbers = dict(
        (
            await session.execute(
                sa.select(ModelVersion.id, ModelVersion.version).where(
                    ModelVersion.id.in_([d.version_id for d in deployments])
                )
            )
        ).all()
    )
    slugs = dict(
        (
            await session.execute(
                sa.select(Model.id, Model.slug).where(
                    Model.id.in_([d.model_id for d in deployments])
                )
            )
        ).all()
    )

    for deployment in deployments:
        state = counts.get(deployment.id, {})
        ready = state.get("ready", 0)
        failed = state.get("failed", 0)
        pending = state.get("pending", 0) + state.get("loading", 0) + state.get("warming", 0)

        if deployment.status == "draining":
            new_status, detail = ("stopped" if not (ready or pending) else "draining"), ""
        elif deployment.replicas == 0:
            new_status, detail = "stopped", "scaled to zero"
        elif ready >= deployment.replicas:
            new_status, detail = "live", ""
        elif ready > 0:
            new_status = "live"
            detail = f"{ready}/{deployment.replicas} replicas ready"
        elif pending:
            new_status, detail = "warming", f"{pending} replica(s) loading"
        elif failed:
            new_status, detail = "failed", "every replica failed to load; check the runtime logs"
        else:
            new_status, detail = "pending", "waiting for a runtime with capacity"

        if new_status != deployment.status or detail != deployment.status_detail:
            deployment.status = new_status
            deployment.status_detail = detail
            if new_status == "stopped":
                deployment.stopped_at = datetime.now(UTC)

        label = slugs.get(deployment.model_id, deployment.model_id)
        number = str(version_numbers.get(deployment.version_id, "?"))
        replicas_ready.labels(label, number).set(ready)
        replicas_desired.labels(label, number).set(deployment.replicas)

    await session.commit()


async def assignments_for_runtime(session: AsyncSession, runtime_id: str) -> list[dict]:
    """What the runtime should be holding, with everything it needs to load it."""
    rows = (
        await session.execute(
            sa.select(Assignment, Deployment, ModelVersion, Model)
            .join(Deployment, Deployment.id == Assignment.deployment_id)
            .join(ModelVersion, ModelVersion.id == Assignment.version_id)
            .join(Model, Model.id == Deployment.model_id)
            .where(Assignment.runtime_id == runtime_id, Assignment.state != "failed")
        )
    ).all()

    out = []
    for assignment, deployment, version, model in rows:
        out.append({
            "assignment_id": assignment.id,
            "version_id": version.id,
            "model_id": model.id,
            "model_slug": model.slug,
            "version": version.version,
            "artifact_format": version.artifact_format,
            "weights_key": version.weights_key,
            "weights_bucket": settings.s3_bucket_weights,
            "weights_sha256": version.weights_sha256,
            "config": version.config,
            "device": deployment.device,
            "batching": {
                "max_batch_size": deployment.max_batch_size,
                "max_wait_ms": deployment.max_wait_ms,
                "max_queue_depth": deployment.max_queue_depth,
                "request_timeout_ms": deployment.request_timeout_ms,
            },
            "warmup": (version.config or {}).get("warmup", {"iterations": 3}),
            "desired_state": "draining" if deployment.status == "draining" else "ready",
        })
    return out


async def apply_runtime_report(session: AsyncSession, runtime_id: str,
                               loaded: list[dict]) -> None:
    """Fold a runtime's self-reported load state back into its assignments."""
    by_version = {item.get("version_id"): item for item in loaded}
    rows = (
        await session.execute(
            sa.select(Assignment).where(Assignment.runtime_id == runtime_id)
        )
    ).scalars().all()
    for assignment in rows:
        report = by_version.get(assignment.version_id)
        if report is None:
            if assignment.state == "ready":
                assignment.state = "pending"  # runtime restarted; it must load again
                assignment.ready_at = None
            continue
        state = report.get("state", "pending")
        if state != assignment.state:
            assignment.state = state
            assignment.detail = (report.get("detail") or "")[:2000]
            if state == "ready":
                assignment.ready_at = datetime.now(UTC)
            await _record_load(session, assignment, state, report.get("detail") or "")
    await session.commit()


async def _record_load(session: AsyncSession, assignment: Assignment,
                       state: str, detail: str) -> None:
    """A version is proven by loading, not by a reference image.

    Checking a model against a sample needs a sample, and a synthetic stand-in
    only produces confident nonsense - "nothing was detected" says something
    about the placeholder, not the weights. What can be checked without any data
    is the part that actually blocks serving: whether a runtime can build the
    graph and warm it up. The runtime has to do that anyway to serve, so its
    report is the check, and the version's state follows it.
    """
    version = await session.get(ModelVersion, assignment.version_id)
    if version is None:
        return
    now = datetime.now(UTC)

    if state == "ready":
        if version.status in ("draft", "validating", "failed"):
            version.status = "ready"
            version.published_at = version.published_at or now
        version.validation = {
            "status": "passed",
            "source": "runtime-load",
            "checked_at": now.isoformat(),
            "runtime_id": assignment.runtime_id,
            "errors": [],
            "warnings": [],
        }
    elif state == "failed":
        # Only the last replica standing decides: one runtime failing while
        # another serves it is a fleet problem, not a bad artifact.
        others = (
            await session.execute(
                sa.select(sa.func.count()).select_from(Assignment).where(
                    Assignment.version_id == assignment.version_id,
                    Assignment.id != assignment.id,
                    Assignment.state == "ready",
                )
            )
        ).scalar_one()
        if not others:
            version.status = "failed"
            version.validation = {
                "status": "failed",
                "source": "runtime-load",
                "checked_at": now.isoformat(),
                "runtime_id": assignment.runtime_id,
                "errors": [detail or "the runtime could not load these weights"],
                "warnings": [],
            }


async def autoscale(session: AsyncSession) -> list[dict]:
    """R4.8 - queue-depth driven scaling inside explicit min/max bounds."""
    deployments = list(
        (
            await session.execute(
                sa.select(Deployment).where(
                    Deployment.autoscale.is_(True), Deployment.status.in_(("live", "warming"))
                )
            )
        ).scalars().all()
    )
    changes = []
    for deployment in deployments:
        replicas = await healthy_runtimes_for_version(deployment.version_id)
        if not replicas:
            continue
        depths = []
        for replica in replicas:
            queues = (replica.get("queues") or {})
            depths.append(int(queues.get(deployment.version_id, 0)))
        avg_depth = sum(depths) / max(len(depths), 1)
        target = deployment.target_queue_depth
        want = deployment.replicas

        if avg_depth > target * 1.5 and deployment.replicas < deployment.max_replicas:
            want = min(deployment.max_replicas, deployment.replicas + 1)
        elif avg_depth < max(target * 0.25, 1) and deployment.replicas > deployment.min_replicas:
            want = max(deployment.min_replicas, deployment.replicas - 1)

        if want != deployment.replicas:
            changes.append({
                "deployment_id": deployment.id, "from": deployment.replicas, "to": want,
                "avg_queue_depth": round(avg_depth, 2),
            })
            deployment.replicas = want
    if changes:
        await session.commit()
        await reconcile(session)
    return changes
