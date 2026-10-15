"""R1.x + R2.x - versions: upload, config, validation, publish, compare, lifecycle."""

from __future__ import annotations

import base64
import hashlib
import logging
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from aperture_core import preset_config, preset_summaries
from aperture_core.configspec import ModelConfig
from aperture_core.sandbox import validate_source
from app import audit, storage
from app.db import get_session
from app.deps import Principal, current_user, get_model_or_404, project_dep
from app.errors import ApiError
from app.services.artifact_state import guard_servable
from app.models import (
    Assignment,
    Deployment,
    Job,
    Model,
    ModelVersion,
    Project,
    Tag,
    UploadSession,
)
from app.schemas import (
    ConversionIn,
    RoboflowImportIn,
    HookValidateIn,
    HookValidateOut,
    JobOut,
    PresetOut,
    UploadInit,
    UploadSessionOut,
    VersionCompare,
    VersionCreate,
    VersionOut,
    VersionPatch,
)
from app.serialize import dump
from app.services.analytics import version_traffic
from app.settings import settings

log = logging.getLogger("aperture.versions")
router = APIRouter(tags=["versions"])

ALLOWED_SUFFIXES = {
    ".onnx": "onnx", ".pt": "pt", ".pth": "pt", ".torchscript": "torchscript",
    ".ts": "torchscript", ".safetensors": "safetensors", ".engine": "tensorrt",
    ".plan": "tensorrt", ".trt": "tensorrt",
}
IMMUTABLE_AFTER_PUBLISH = ("config",)


# --------------------------------------------------------------------- helpers


async def _model(session: AsyncSession, project: Project, ref: str,
                 principal: Principal, role: str) -> Model:
    principal.require(project.id, role)
    return await get_model_or_404(session, project.id, ref)


async def _version(session: AsyncSession, model: Model, number: str) -> ModelVersion:
    raw = number[1:] if number[:1].lower() == "v" else number
    if not raw.isdigit():
        raise ApiError("invalid_request", f"'{number}' is not a version number")
    found = (
        await session.execute(
            sa.select(ModelVersion).where(ModelVersion.model_id == model.id,
                                          ModelVersion.version == int(raw))
        )
    ).scalar_one_or_none()
    if found is None:
        raise ApiError("version_not_found", f"{model.slug} has no version {raw}")
    return found


async def _out(session: AsyncSession, version: ModelVersion) -> VersionOut:
    tags = list(
        (
            await session.execute(sa.select(Tag.name).where(Tag.version_id == version.id))
        ).scalars().all()
    )
    deployment = (
        await session.execute(
            sa.select(Deployment).where(Deployment.version_id == version.id,
                                        Deployment.mode == "primary")
        )
    ).scalar_one_or_none()
    payload = VersionOut.model_validate(await dump(session, version, tags=tags))
    if deployment is not None:
        from app.api.v1.deployments import deployment_out

        payload.deployment = await deployment_out(session, deployment, version.version)
    return payload


def _apply_artifact(version: ModelVersion, artifact: dict) -> None:
    """Record what was uploaded, and pre-empt a validation that cannot succeed.

    A bare state_dict has no graph, so no runtime can ever execute it. Saying so
    now - with the architecture we recognised and how to export it - is far more
    use than letting someone ship 150 MB to a runtime and read "needs PyTorch".
    """
    version.artifact_info = artifact
    if artifact.get("usability") in ("weights_only", "unknown"):
        version.status = "failed"
        version.validation = {
            "status": "failed",
            "source": "artifact-inspection",
            "checked_at": datetime.now(UTC).isoformat(),
            "errors": [artifact.get("detail", "this file cannot be served")],
            "warnings": [artifact.get("next_step", "")] if artifact.get("next_step") else [],
            "artifact": artifact,
        }
    elif artifact.get("usability") == "convertible":
        # convertible is not a failure - there is a button for it
        if version.status == "failed":
            version.status = "draft"
        version.validation = {
            "status": "pending",
            "source": "artifact-inspection",
            "warnings": [artifact.get("next_step", "")] if artifact.get("next_step") else [],
            "artifact": artifact,
        }


async def _autoconvert(session: AsyncSession, project_id: str, model: Model,
                       version: ModelVersion, principal: Principal,
                       client_ip: str | None) -> Job | None:
    """Queue a conversion only when nothing in the fleet can run the file as it is.

    ONNX is an option, not a requirement. A `.pt` that carries a graph is served
    directly by a runtime that has torch, and placement routes it there - so
    converting is a choice about artifact size and dependencies, and imposing it
    on upload would be deciding that for someone.

    What is worth automating is the case where the file *cannot* run anywhere: no
    torch-capable runtime is up, so the upload would sit at a red failure with a
    button next to it. Then the conversion is queued, because the alternative is
    a dead end rather than a trade-off.
    """
    from app.jobs.queue import QUEUE_CONVERT, worker_alive

    artifact = version.artifact_info or {}
    # An RF-DETR state_dict is servable, but only where `rfdetr` is installed, so
    # it belongs in this path too: on a fleet without it, the upload is the same
    # dead end a convertible artifact would be.
    exportable = artifact.get("usability") == "convertible" or (
        artifact.get("kind") == "state_dict" and artifact.get("family") == "rf-detr"
    )
    if not exportable:
        return None
    if await _fleet_can_load(session, version.artifact_format, artifact):
        return None
    if not await worker_alive(QUEUE_CONVERT):
        return None

    job = Job(project_id=project_id, kind="convert", status="queued", created_by=principal.id,
              params={"model_id": model.id, "version_id": version.id, "target": "onnx",
                      "automatic": True})
    session.add(job)
    await session.flush()
    await audit.record(session, action="version.convert", project_id=project_id,
                       actor_id=principal.id, actor_label=principal.label,
                       target_type="version", target_id=version.id,
                       target_label=f"{model.slug} v{version.version}",
                       payload={"target": "onnx", "automatic": True},
                       client_ip=client_ip)
    version.validation = {
        **(version.validation or {}),
        "status": "pending",
        "warnings": [
            f"no runtime in the fleet can load a '{version.artifact_format}' artifact, so it is "
            f"being converted to ONNX (job {job.id[:8]}) - the result lands as a new draft "
            "version. To serve this file as it is instead, start a torch runtime: "
            "docker compose --profile torch up -d runtime-torch"
        ],
    }
    return job


async def _dispatch_conversion(session: AsyncSession, job: Job | None) -> None:
    """Queue an automatic conversion once the upload that asked for it is committed.

    The upload itself succeeded, so a queue that is down fails the conversion job
    (it shows as failed on the jobs page) rather than the request.
    """
    if job is None:
        return
    from app.jobs.queue import QUEUE_CONVERT, dispatch

    await dispatch(session, job, "convert_version", queue=QUEUE_CONVERT)


async def _fleet_can_load(session: AsyncSession, fmt: str | None,
                          artifact: dict | None = None) -> bool:
    """Is there a live runtime whose image can execute this artifact?"""
    from app.services.deployer import _can_load, _fresh_runtimes, _requirement

    if not _requirement(fmt, artifact):
        return True
    return any(_can_load(runtime, fmt, artifact) for runtime in await _fresh_runtimes(session))


def _artifact_format(filename: str, declared: str) -> str:
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    detected = ALLOWED_SUFFIXES.get(suffix)
    if detected is None:
        raise ApiError(
            "unsupported_format",
            f"'{suffix or filename}' is not a supported weights file; expected one of "
            + ", ".join(sorted(ALLOWED_SUFFIXES)),
        )
    if declared and declared != "other" and detected != declared:
        log.info("declared format differs from the file suffix",
                 extra={"declared": declared, "detected": detected})
    return detected


# ------------------------------------------------------------------- listing


@router.get("/meta/presets", response_model=list[PresetOut], tags=["meta"])
async def presets() -> list[PresetOut]:
    """R1.5 - what the upload wizard offers."""
    return [PresetOut(**p) for p in preset_summaries()]


@router.get("/meta/presets/{name}", tags=["meta"])
async def preset_detail(name: str) -> dict:
    try:
        return preset_config(name).model_dump(mode="json")
    except KeyError as exc:
        raise ApiError("not_found", f"no preset named '{name}'") from exc


@router.post("/meta/hooks/validate", response_model=HookValidateOut, tags=["meta"])
async def validate_hook(body: HookValidateIn,
                        principal: Principal = Depends(current_user)) -> HookValidateOut:
    """R1.9/R7.8 - static screen of a custom hook before it is ever executed."""
    problems = validate_source(body.source)
    expected = "preprocess" if body.kind == "preprocess" else "postprocess"
    if f"def {expected}" not in body.source:
        problems.append(f"the hook must define a function named '{expected}'")
    return HookValidateOut(ok=not problems, problems=problems)


@router.get("/models/{ref}/versions", response_model=list[VersionOut])
async def list_versions(ref: str, include_archived: bool = False,
                        project: Project = Depends(project_dep),
                        principal: Principal = Depends(current_user),
                        session: AsyncSession = Depends(get_session)) -> list[VersionOut]:
    model = await _model(session, project, ref, principal, "viewer")
    stmt = sa.select(ModelVersion).where(ModelVersion.model_id == model.id)
    if not include_archived:
        stmt = stmt.where(ModelVersion.status != "archived")
    versions = list((await session.execute(stmt.order_by(ModelVersion.version.desc()))).scalars())
    return [await _out(session, v) for v in versions]


@router.post("/models/{ref}/versions", response_model=VersionOut, status_code=201)
async def create_version(ref: str, body: VersionCreate, request: Request,
                         project: Project = Depends(project_dep),
                         principal: Principal = Depends(current_user),
                         session: AsyncSession = Depends(get_session)) -> VersionOut:
    """R2.1 - versions auto-increment; R1.6 - config is inherited, then editable."""
    model = await _model(session, project, ref, principal, "developer")

    latest = (
        await session.execute(
            sa.select(ModelVersion).where(ModelVersion.model_id == model.id)
            .order_by(ModelVersion.version.desc()).limit(1)
        )
    ).scalar_one_or_none()
    number = (latest.version if latest else 0) + 1

    parent = None
    if body.config is not None:
        config = body.config
    elif body.inherit_from_version is not None:
        parent = await _version(session, model, str(body.inherit_from_version))
        config = ModelConfig.model_validate(parent.config)
    elif body.preset:
        try:
            config = preset_config(body.preset)
        except KeyError as exc:
            raise ApiError("invalid_request", f"no preset named '{body.preset}'") from exc
    elif latest is not None:
        parent = latest
        config = ModelConfig.model_validate(latest.config)
    else:
        raise ApiError(
            "invalid_request",
            "the first version needs a preset or an explicit config - pick the architecture "
            "family that matches these weights",
        )

    if config.task.value != model.task:
        config = config.model_copy(update={"task": model.task})

    version = ModelVersion(
        model_id=model.id, version=number, status="draft", preset=body.preset,
        config=config.model_dump(mode="json"), artifact_format=body.artifact_format,
        training_run_id=body.training_run_id, dataset_version=body.dataset_version,
        metrics=body.metrics, git_commit=body.git_commit, changelog=body.changelog,
        parent_version_id=parent.id if parent else None, created_by=principal.id,
    )
    session.add(version)
    await session.flush()
    await audit.record(session, action="version.upload_start", project_id=project.id,
                       actor_id=principal.id, actor_label=principal.label, target_type="version",
                       target_id=version.id, target_label=f"{model.slug} v{number}",
                       payload={"preset": body.preset,
                                "inherited_from": parent.version if parent else None},
                       client_ip=request.client.host if request.client else None)
    await session.commit()
    return await _out(session, version)


@router.get("/models/{ref}/versions/{number}", response_model=VersionOut)
async def get_version(ref: str, number: str, project: Project = Depends(project_dep),
                      principal: Principal = Depends(current_user),
                      session: AsyncSession = Depends(get_session)) -> VersionOut:
    model = await _model(session, project, ref, principal, "viewer")
    return await _out(session, await _version(session, model, number))


@router.patch("/models/{ref}/versions/{number}", response_model=VersionOut)
async def patch_version(ref: str, number: str, body: VersionPatch, request: Request,
                        project: Project = Depends(project_dep),
                        principal: Principal = Depends(current_user),
                        session: AsyncSession = Depends(get_session)) -> VersionOut:
    """Provenance stays editable; the config freezes at publish (R2.1)."""
    model = await _model(session, project, ref, principal, "developer")
    version = await _version(session, model, number)
    changes = body.model_dump(exclude_unset=True)

    if "config" in changes and changes["config"] is not None:
        if version.status not in ("draft", "failed", "validating"):
            raise ApiError(
                "version_immutable",
                f"version {version.version} is published - create a new version instead of "
                "editing its preprocessing (R2.1)",
                details={"status": version.status},
            )
        config = ModelConfig.model_validate(changes.pop("config"))
        version.config = config.model_dump(mode="json")
        version.validation = {**(version.validation or {}), "status": "pending",
                              "stale": True}
    for key, value in changes.items():
        setattr(version, key, value)

    await audit.record(
        session, action="version.config_update", project_id=project.id, actor_id=principal.id,
        actor_label=principal.label, target_type="version", target_id=version.id,
        target_label=f"{model.slug} v{version.version}",
        payload={"fields": sorted(changes) + (["config"] if body.config is not None else [])},
        client_ip=request.client.host if request.client else None,
    )
    await session.commit()
    return await _out(session, version)


# -------------------------------------------------------------------- uploads


@router.post("/models/{ref}/versions/{number}/uploads", response_model=UploadSessionOut,
             status_code=201)
async def start_upload(ref: str, number: str, body: UploadInit, request: Request,
                       project: Project = Depends(project_dep),
                       principal: Principal = Depends(current_user),
                       session: AsyncSession = Depends(get_session)) -> UploadSessionOut:
    """R1.1 - open a resumable multipart upload for this version's weights."""
    model = await _model(session, project, ref, principal, "developer")
    version = await _version(session, model, number)
    if version.status not in ("draft", "failed"):
        raise ApiError("version_immutable",
                       f"version {version.version} already has weights and is {version.status}")
    if body.total_bytes > settings.max_upload_mb * 1024 * 1024:
        raise ApiError("payload_too_large",
                       f"weights of {body.total_bytes / 1e9:.1f} GB exceed the "
                       f"{settings.max_upload_mb / 1024:.0f} GB limit")

    fmt = _artifact_format(body.filename, version.artifact_format)
    key = storage.weights_key(project.slug, model.slug, version.version, body.filename)
    chunk = body.chunk_bytes or settings.upload_chunk_mb * 1024 * 1024
    multipart_id = await storage.create_multipart(settings.s3_bucket_weights, key)

    upload = UploadSession(
        project_id=project.id, model_id=model.id, filename=body.filename,
        total_bytes=body.total_bytes, chunk_bytes=chunk, key=key, multipart_id=multipart_id,
        sha256_expected=body.sha256, created_by=principal.id,
        expires_at=datetime.now(UTC) + timedelta(days=2),
    )
    session.add(upload)
    version.artifact_format = fmt
    await session.commit()
    return UploadSessionOut.model_validate(
        await dump(session, upload, next_part=1, received_parts=[])
    )


@router.get("/uploads/{upload_id}", response_model=UploadSessionOut)
async def upload_status(upload_id: str, principal: Principal = Depends(current_user),
                        session: AsyncSession = Depends(get_session)) -> UploadSessionOut:
    """Resume support: the client asks which parts already landed."""
    upload = await session.get(UploadSession, upload_id)
    if upload is None:
        raise ApiError("not_found", "no such upload session")
    principal.require(upload.project_id, "developer")
    parts = sorted(p["part"] for p in (upload.parts or []))
    return UploadSessionOut.model_validate(
        await dump(session, upload, received_parts=parts,
                   next_part=(max(parts) + 1) if parts else 1)
    )


@router.put("/uploads/{upload_id}/parts/{part_number}", response_model=UploadSessionOut)
async def upload_part(upload_id: str, part_number: int, chunk: UploadFile = File(...),
                      principal: Principal = Depends(current_user),
                      session: AsyncSession = Depends(get_session)) -> UploadSessionOut:
    upload = await session.get(UploadSession, upload_id)
    if upload is None:
        raise ApiError("not_found", "no such upload session")
    principal.require(upload.project_id, "developer")
    if upload.status != "open":
        raise ApiError("conflict", f"this upload session is {upload.status}")
    if part_number < 1 or part_number > 10_000:
        raise ApiError("invalid_request", "part numbers run from 1 to 10000")

    data = await chunk.read()
    if not data:
        raise ApiError("invalid_request", "the chunk is empty")
    etag = await storage.upload_part(settings.s3_bucket_weights, upload.key,
                                     upload.multipart_id, part_number, data)

    parts = [p for p in (upload.parts or []) if p["part"] != part_number]
    parts.append({"part": part_number, "etag": etag, "size": len(data)})
    upload.parts = parts
    upload.received_bytes = sum(p["size"] for p in parts)
    await session.commit()

    numbers = sorted(p["part"] for p in parts)
    return UploadSessionOut.model_validate(
        await dump(session, upload, received_parts=numbers, next_part=max(numbers) + 1)
    )


@router.post("/uploads/{upload_id}/complete", response_model=VersionOut)
async def complete_upload(upload_id: str, request: Request,
                          principal: Principal = Depends(current_user),
                          session: AsyncSession = Depends(get_session)) -> VersionOut:
    """R1.8 - finish the multipart upload, then verify the stored bytes end to end."""
    upload = await session.get(UploadSession, upload_id)
    if upload is None:
        raise ApiError("not_found", "no such upload session")
    principal.require(upload.project_id, "developer")
    if not upload.parts:
        raise ApiError("upload_incomplete", "no chunks were received for this upload")

    model = await session.get(Model, upload.model_id)
    version = (
        await session.execute(
            sa.select(ModelVersion).where(ModelVersion.model_id == upload.model_id,
                                          ModelVersion.status.in_(("draft", "failed")))
            .order_by(ModelVersion.version.desc()).limit(1)
        )
    ).scalar_one_or_none()
    if version is None or model is None:
        raise ApiError("conflict", "this upload has no draft version to attach to")

    await storage.complete_multipart(settings.s3_bucket_weights, upload.key,
                                     upload.multipart_id, upload.parts)
    digest, size, artifact = await storage.verify_and_inspect(settings.s3_bucket_weights,
                                                             upload.key)
    if upload.sha256_expected and digest != upload.sha256_expected:
        await storage.delete_object(settings.s3_bucket_weights, upload.key)
        upload.status = "failed"
        await session.commit()
        raise ApiError(
            "validation_failed",
            "the stored weights do not match the checksum you sent - the upload was discarded",
            details={"expected": upload.sha256_expected, "actual": digest},
        )
    if upload.total_bytes and size != upload.total_bytes:
        raise ApiError(
            "upload_incomplete",
            f"stored {size} bytes but the session declared {upload.total_bytes}; "
            "re-send the missing chunks",
            details={"received_bytes": size, "declared_bytes": upload.total_bytes},
        )

    upload.status = "complete"
    upload.received_bytes = size
    version.weights_key = upload.key
    version.weights_bytes = size
    version.weights_sha256 = digest
    version.status = "draft"
    _apply_artifact(version, artifact)
    convert_job = await _autoconvert(session, upload.project_id, model, version, principal,
                                     request.client.host if request.client else None)

    await audit.record(session, action="version.upload_complete", project_id=upload.project_id,
                       actor_id=principal.id, actor_label=principal.label, target_type="version",
                       target_id=version.id, target_label=f"{model.slug} v{version.version}",
                       payload={"bytes": size, "sha256": digest, "key": upload.key,
                                "artifact": artifact.get("kind"),
                                "usability": artifact.get("usability")},
                       client_ip=request.client.host if request.client else None)
    await session.commit()
    await _dispatch_conversion(session, convert_job)
    return await _out(session, version)


@router.post("/models/{ref}/versions/{number}/weights", response_model=VersionOut)
async def upload_weights_single_shot(
    ref: str, number: str, request: Request, file: UploadFile = File(...),
    sha256: str | None = Query(None, min_length=64, max_length=64),
    project: Project = Depends(project_dep),
    principal: Principal = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> VersionOut:
    """Convenience path for small artifacts; large files should use the chunked flow."""
    model = await _model(session, project, ref, principal, "developer")
    version = await _version(session, model, number)
    if version.status not in ("draft", "failed"):
        raise ApiError("version_immutable", f"version {version.version} is {version.status}")

    fmt = _artifact_format(file.filename or "weights.onnx", version.artifact_format)
    data = await file.read()
    limit = settings.max_upload_mb * 1024 * 1024
    if len(data) > limit:
        raise ApiError("payload_too_large", "use the chunked upload endpoints for this file size")
    digest = hashlib.sha256(data).hexdigest()
    if sha256 and digest != sha256:
        raise ApiError("validation_failed", "the uploaded bytes do not match the checksum",
                       details={"expected": sha256, "actual": digest})

    key = storage.weights_key(project.slug, model.slug, version.version,
                              file.filename or "weights.onnx")
    await storage.put_bytes(settings.s3_bucket_weights, key, data)
    _, _, artifact = await storage.verify_and_inspect(settings.s3_bucket_weights, key)
    version.weights_key, version.weights_bytes = key, len(data)
    version.weights_sha256, version.artifact_format = digest, fmt
    _apply_artifact(version, artifact)
    convert_job = await _autoconvert(session, project.id, model, version, principal,
                                     request.client.host if request.client else None)

    await audit.record(session, action="version.upload_complete", project_id=project.id,
                       actor_id=principal.id, actor_label=principal.label, target_type="version",
                       target_id=version.id, target_label=f"{model.slug} v{version.version}",
                       payload={"bytes": len(data), "sha256": digest},
                       client_ip=request.client.host if request.client else None)
    await session.commit()
    await _dispatch_conversion(session, convert_job)
    return await _out(session, version)


@router.get("/models/{ref}/versions/{number}/download")
async def download_weights(ref: str, number: str, project: Project = Depends(project_dep),
                           principal: Principal = Depends(current_user),
                           session: AsyncSession = Depends(get_session)) -> dict:
    model = await _model(session, project, ref, principal, "developer")
    version = await _version(session, model, number)
    if not version.weights_key:
        raise ApiError("not_found", "this version has no weights yet")
    url = await storage.presign_get(settings.s3_bucket_weights, version.weights_key,
                                    expires=900, public=True)
    return {"url": url, "expires_in": 900, "sha256": version.weights_sha256,
            "bytes": version.weights_bytes}


# ----------------------------------------------------------------- validation


@router.post("/models/{ref}/versions/{number}/publish", response_model=VersionOut)
async def publish_version(ref: str, number: str, request: Request,
                          force: bool = Query(False, description="publish despite a failed check"),
                          project: Project = Depends(project_dep),
                          principal: Principal = Depends(current_user),
                          session: AsyncSession = Depends(get_session)) -> VersionOut:
    model = await _model(session, project, ref, principal, "developer")
    version = await _version(session, model, number)
    if not version.weights_key:
        raise ApiError("upload_incomplete", "upload the weights before publishing")
    # No gate on a run against a reference image: that needs a dataset, and a
    # synthetic stand-in judges the placeholder rather than the weights. What can
    # be known without data is whether the file carries a graph at all.
    if not force:
        await guard_servable(session, version)
    version.status = "ready"
    version.published_at = version.published_at or datetime.now(UTC)
    await audit.record(session, action="version.publish", project_id=project.id,
                       actor_id=principal.id, actor_label=principal.label, target_type="version",
                       target_id=version.id, target_label=f"{model.slug} v{version.version}",
                       payload={"forced": force},
                       client_ip=request.client.host if request.client else None)
    await session.commit()
    return await _out(session, version)


# ------------------------------------------------------------------ lifecycle


@router.post("/models/{ref}/versions/{number}/deprecate", response_model=VersionOut)
async def deprecate_version(ref: str, number: str, request: Request,
                            project: Project = Depends(project_dep),
                            principal: Principal = Depends(current_user),
                            session: AsyncSession = Depends(get_session)) -> VersionOut:
    """R2.6 - mark a version as on its way out without breaking callers pinned to it."""
    model = await _model(session, project, ref, principal, "developer")
    version = await _version(session, model, number)
    tags = list(
        (await session.execute(sa.select(Tag.name).where(Tag.version_id == version.id)))
        .scalars().all()
    )
    if tags:
        raise ApiError("conflict",
                       f"move these tags off version {version.version} first: {', '.join(tags)}")
    version.status = "deprecated"
    version.deprecated_at = datetime.now(UTC)
    await audit.record(session, action="version.deprecate", project_id=project.id,
                       actor_id=principal.id, actor_label=principal.label, target_type="version",
                       target_id=version.id, target_label=f"{model.slug} v{version.version}",
                       client_ip=request.client.host if request.client else None)
    await session.commit()
    return await _out(session, version)


@router.post("/models/{ref}/versions/{number}/archive", response_model=VersionOut)
async def archive_version(ref: str, number: str, request: Request,
                          purge_weights: bool = Query(False),
                          project: Project = Depends(project_dep),
                          principal: Principal = Depends(current_user),
                          session: AsyncSession = Depends(get_session)) -> VersionOut:
    """R2.6 - archive, optionally reclaiming the weights blob."""
    model = await _model(session, project, ref, principal, "admin")
    version = await _version(session, model, number)
    live = (
        await session.execute(
            sa.select(sa.func.count()).select_from(Deployment)
            .where(Deployment.version_id == version.id, Deployment.status != "stopped")
        )
    ).scalar_one()
    if live:
        raise ApiError("conflict", "stop this version's deployment before archiving it")

    if purge_weights and version.weights_key:
        await storage.delete_object(settings.s3_bucket_weights, version.weights_key)
        version.weights_key = None
    version.status = "archived"
    await audit.record(session, action="version.archive", project_id=project.id,
                       actor_id=principal.id, actor_label=principal.label, target_type="version",
                       target_id=version.id, target_label=f"{model.slug} v{version.version}",
                       payload={"purge_weights": purge_weights},
                       client_ip=request.client.host if request.client else None)
    await session.commit()
    return await _out(session, version)


# -------------------------------------------------------------------- compare


@router.get("/models/{ref}/version-compare", response_model=VersionCompare)
async def compare_versions(ref: str, left: int, right: int,
                           project: Project = Depends(project_dep),
                           principal: Principal = Depends(current_user),
                           session: AsyncSession = Depends(get_session)) -> VersionCompare:
    """R2.5 - metadata, metrics, latency and config differences side by side."""
    model = await _model(session, project, ref, principal, "viewer")
    a = await _version(session, model, str(left))
    b = await _version(session, model, str(right))

    diff = _diff_config(a.config or {}, b.config or {})
    metrics = sorted(set(a.metrics or {}) | set(b.metrics or {}))
    metric_diff = {
        key: {"left": (a.metrics or {}).get(key), "right": (b.metrics or {}).get(key),
              "delta": _delta((a.metrics or {}).get(key), (b.metrics or {}).get(key))}
        for key in metrics
    }
    traffic = await version_traffic(session, [a.id, b.id], hours=24 * 7)
    return VersionCompare(
        left=await _out(session, a), right=await _out(session, b), config_diff=diff,
        metric_diff=metric_diff,
        traffic={"left": traffic.get(a.id, {}), "right": traffic.get(b.id, {})},
    )


def _delta(left, right):
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return round(right - left, 6)
    return None


def _diff_config(left: dict, right: dict, prefix: str = "") -> list[dict]:
    out: list[dict] = []
    for key in sorted(set(left) | set(right)):
        path = f"{prefix}{key}"
        lv, rv = left.get(key), right.get(key)
        if isinstance(lv, dict) and isinstance(rv, dict):
            out.extend(_diff_config(lv, rv, prefix=f"{path}."))
        elif lv != rv:
            if isinstance(lv, list) and isinstance(rv, list) and len(lv) + len(rv) > 20:
                out.append({"path": path, "left": f"{len(lv)} entries",
                            "right": f"{len(rv)} entries", "kind": "list_size"})
            else:
                out.append({"path": path, "left": lv, "right": rv, "kind": "value"})
    return out


# -------------------------------------------------------------- roboflow import


@router.post("/imports/roboflow", response_model=JobOut, status_code=202, tags=["models"])
async def import_from_roboflow(body: RoboflowImportIn, request: Request,
                               project: Project = Depends(project_dep),
                               principal: Principal = Depends(current_user),
                               session: AsyncSession = Depends(get_session)) -> JobOut:
    """R1.11 - import a trained Roboflow model, names and all.

    Uploading weights by hand leaves two things to get right that nothing checks:
    the class list has to come from the *same* version the weights did - two
    versions of one project order their classes differently - and RF-DETR's dead
    leading logit column has to be accounted for. Get either wrong and every
    prediction is mislabelled while the model looks perfectly healthy.

    Both are knowable, so this reads them: the ordered class list from the
    version being imported, and the offset measured on the exported graph.
    """
    principal.require(project.id, "developer")
    from app.jobs.tasks import parse_roboflow_ref

    workspace, rf_project, rf_version = parse_roboflow_ref(body.ref)

    if not (body.api_key or settings.roboflow_api_key):
        raise ApiError(
            "invalid_config",
            "no Roboflow API key. Set AP_ROBOFLOW_API_KEY on the instance, or send "
            "`api_key` with this request.",
        )

    from app.jobs.queue import QUEUE_CONVERT, submit, worker_alive

    if not await worker_alive(QUEUE_CONVERT):
        raise ApiError(
            "runtime_unhealthy",
            "no converter is running. Importing needs torch and the Roboflow SDK, which "
            "the serving images deliberately leave out - start it with "
            "`docker compose --profile convert up -d converter`.",
        )

    # the key is a credential: it rides along with the queued task in Redis and never
    # lands in the job row, which every project viewer can read back
    params = body.model_dump(exclude={"api_key"})
    params.update(workspace=workspace, project=rf_project, version=rf_version)
    job = Job(project_id=project.id, kind="roboflow_import", status="queued",
              created_by=principal.id, params=params)
    session.add(job)
    await session.flush()
    # same in the audit trail: recorded as present rather than written down
    await audit.record(session, action="model.import", project_id=project.id,
                       actor_id=principal.id, actor_label=principal.label, target_type="model",
                       target_id=job.id, target_label=f"{workspace}/{rf_project}/{rf_version}",
                       payload={"ref": body.ref, "slug": body.slug, "preset": body.preset,
                                "own_api_key": bool(body.api_key)},
                       client_ip=request.client.host if request.client else None)
    await session.commit()
    await submit(session, job, "import_roboflow", queue=QUEUE_CONVERT,
                 api_key=body.api_key)
    return JobOut.model_validate(job)


# ----------------------------------------------------------------- conversion


@router.post("/models/{ref}/versions/{number}/convert", response_model=JobOut, status_code=202)
async def convert_version(ref: str, number: str, body: ConversionIn, request: Request,
                          project: Project = Depends(project_dep),
                          principal: Principal = Depends(current_user),
                          session: AsyncSession = Depends(get_session)) -> JobOut:
    """R1.10 - queue an export/quantisation; the result lands as a new draft version."""
    model = await _model(session, project, ref, principal, "developer")
    version = await _version(session, model, number)
    if not version.weights_key:
        raise ApiError("upload_incomplete", "this version has no weights to convert")

    artifact = version.artifact_info or {}
    if artifact.get("usability") in ("weights_only", "unknown"):
        raise ApiError(
            "invalid_request",
            (artifact.get("detail") or "this artifact carries no model graph")
            + " " + (artifact.get("next_step") or ""),
            details={"artifact": artifact},
        )

    from app.jobs.queue import QUEUE_CONVERT, submit, worker_alive

    if not await worker_alive(QUEUE_CONVERT):
        raise ApiError(
            "runtime_unhealthy",
            "no converter is running. Conversion needs torch and ultralytics, which the "
            "serving images deliberately leave out - start it with "
            "`docker compose --profile convert up -d converter`.",
        )

    job = Job(project_id=project.id, kind="convert", status="queued", created_by=principal.id,
              params={"model_id": model.id, "version_id": version.id,
                      **body.model_dump()})
    session.add(job)
    await session.flush()
    await audit.record(session, action="version.convert", project_id=project.id,
                       actor_id=principal.id, actor_label=principal.label, target_type="version",
                       target_id=version.id, target_label=f"{model.slug} v{version.version}",
                       payload=body.model_dump(),
                       client_ip=request.client.host if request.client else None)
    await session.commit()
    await submit(session, job, "convert_version", queue=QUEUE_CONVERT)
    return JobOut.model_validate(job)
