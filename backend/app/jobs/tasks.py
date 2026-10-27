"""Background work: async inference, conversions, exports, retention, rollups, alerts.

These run in the arq worker, never in the API process, so a 2-hour video job or a
TensorRT compile cannot touch inference latency.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import subprocess
import tempfile
import zipfile
from datetime import UTC, datetime, timedelta

import httpx
import sqlalchemy as sa
from aperture_core.rfdetr import build as rfdetr_build
from aperture_core.rfdetr import geometry_from_state_dict, rebuild_kwargs

from app import storage
from app.cache import publish_log
from app.db import SessionLocal
from app.errors import ApiError
from app.models import (
    Assignment,
    InferenceLog,
    Job,
    Model,
    ModelVersion,
    ReportSchedule,
    Runtime,
    UploadSession,
    Workspace,
)
from app.services import alerts as alert_service
from app.services import drift as drift_service
from app.services import reports as report_service
from app.services.deployer import autoscale, reconcile
from app.services.gateway import (
    InputImage,
    build_runtime_payload,
    dispatch,
    fetch_url_image,
    resolve_target,
    sanitise_overrides,
)
from app.services.versioning import next_version_number
from app.settings import settings

log = logging.getLogger("aperture.jobs")

FRAME_BATCH = 8


# ---------------------------------------------------------------- job plumbing


async def _start(session, job_id: str) -> Job | None:
    job = await session.get(Job, job_id)
    if job is None:
        log.warning("job vanished", extra={"job_id": job_id})
        return None
    if job.status == "cancelled":
        return None
    job.status = "running"
    job.started_at = datetime.now(UTC)
    await session.commit()
    return job


async def _finish(session, job: Job, *, result: dict | None = None,
                  error: str | None = None) -> None:
    job.status = "failed" if error else "succeeded"
    job.error = error
    job.result = result or {}
    job.finished_at = datetime.now(UTC)
    await session.commit()
    if job.webhook_url:
        await _notify(job)


async def _notify(job: Job) -> None:
    payload = {
        "job_id": job.id, "kind": job.kind, "status": job.status, "progress": job.progress,
        "total": job.total, "error": job.error,
        "result": {k: v for k, v in (job.result or {}).items() if k != "predictions"},
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(job.webhook_url, json=payload)
    except Exception:
        log.warning("job webhook delivery failed", extra={"job_id": job.id}, exc_info=True)


# ------------------------------------------------------------- async inference


async def run_inference_job(ctx: dict, job_id: str) -> dict:
    """R3.8 - bulk folders and video, reusing the exact serving path."""
    async with SessionLocal() as session:
        job = await _start(session, job_id)
        if job is None:
            return {"skipped": True}
        params = job.params or {}
        try:
            model = await session.get(Model, params["model_id"])
            if model is None:
                raise ApiError("not_found", "the model for this job is gone")

            resolved, target, _ = await resolve_target(session, model, params.get("ref", "production"))
            overrides = sanitise_overrides(params.get("overrides"))

            if job.kind == "video":
                frames = await _extract_frames(params["video_key"],
                                               int(params.get("frame_stride", 1)))
                job.total = len(frames)
                await session.commit()
                items = [(f"frame_{i:06d}.jpg", data) for i, data in enumerate(frames)]
            else:
                items = []
                for key in params.get("source_keys") or []:
                    items.append((key.rsplit("/", 1)[-1],
                                  await storage.get_bytes(settings.s3_bucket_captures, key)))
                for url in params.get("source_urls") or []:
                    image = await fetch_url_image(url)
                    items.append((image.name, image.data))
                job.total = len(items)
                await session.commit()

            predictions: list[dict] = []
            failures: list[dict] = []
            for start in range(0, len(items), FRAME_BATCH):
                fresh = await session.get(Job, job.id)
                if fresh is not None and fresh.status == "cancelled":
                    return {"cancelled": True, "processed": len(predictions)}

                chunk = items[start:start + FRAME_BATCH]
                images = [InputImage(data=data, name=name, source="job") for name, data in chunk]
                try:
                    payload = build_runtime_payload(
                        target, images, request_id=f"job-{job.id}-{start}", overrides=overrides,
                        want_overlay=False, extra=None,
                        deadline_ms=target.deployment.request_timeout_ms,
                    )
                    reply = await dispatch(target.version.id, model.slug, payload,
                                           timeout_ms=target.deployment.request_timeout_ms,
                                           trace_headers={})
                    for offset, result in enumerate(reply.results):
                        predictions.append({"source": chunk[offset][0], **result})
                    await publish_log({
                        "request_id": f"job-{job.id}-{start}", "trace_id": None, "span_id": None,
                        "model_id": model.id, "model_slug": model.slug,
                        "version_id": target.version.id, "version_number": target.version.version,
                        "tag": resolved.tag, "route": "primary", "caller_type": "job",
                        "caller_id": job.id, "caller_label": f"job:{job.kind}",
                        "client_ip": None, "status": "ok", "http_status": 200,
                        "error_code": None, "error_message": None, "batch_size": len(chunk),
                        **{f"{k}_ms": reply.latency.get(f"{k}_ms", 0.0)
                           for k in ("queue", "preprocess", "inference", "postprocess",
                                     "gateway", "total")},
                        "runtime_id": reply.runtime_id,
                        "input_ref": {"count": len(chunk), "sources": ["job"]},
                        "input_stats": {}, "output": {}, "confidence": {}, "overrides": overrides,
                        "capture": {}, "detection_count": sum(
                            len(r.get("detections") or []) for r in reply.results
                        ),
                        "purge_after": None, "created_at": datetime.now(UTC).isoformat(),
                    })
                except ApiError as exc:
                    failures.append({"batch_start": start, "code": exc.code,
                                     "message": exc.message})
                    log.warning("job batch failed", extra={"job_id": job.id, "code": exc.code})

                job.progress = min(job.total, start + len(chunk))
                await session.commit()

            artifact = {"kind": job.kind, "model": model.slug,
                        "version": target.version.version, "predictions": predictions,
                        "failures": failures}
            key = f"jobs/{job.id}.json"
            await storage.put_bytes(settings.s3_bucket_reports, key,
                                    json.dumps(artifact, default=str).encode(),
                                    content_type="application/json")
            await _finish(session, job, result={
                "artifact_key": key, "processed": len(predictions),
                "failed_batches": len(failures), "model": model.slug,
                "version": target.version.version,
            })
            return {"processed": len(predictions)}
        except Exception as exc:
            log.exception("inference job failed", extra={"job_id": job_id})
            await _finish(session, job, error=str(exc)[:2000])
            return {"error": str(exc)}


async def _extract_frames(video_key: str, stride: int) -> list[bytes]:
    """ffmpeg is the only sane way to be format-agnostic here."""
    with tempfile.TemporaryDirectory(prefix="aperture-video-") as tmp:
        local = os.path.join(tmp, "input")
        await storage.download_to_path(settings.s3_bucket_captures, video_key, local)
        pattern = os.path.join(tmp, "frame_%06d.jpg")
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", local,
            "-vf", f"select=not(mod(n\\,{max(stride, 1)}))", "-vsync", "vfr",
            "-q:v", "3", pattern,
        ]
        result = subprocess.run(command, capture_output=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(
                "ffmpeg could not decode this video: "
                + result.stderr.decode("utf-8", "replace")[-500:]
            )
        frames = []
        for name in sorted(os.listdir(tmp)):
            if name.startswith("frame_") and name.endswith(".jpg"):
                with open(os.path.join(tmp, name), "rb") as fh:
                    frames.append(fh.read())
        return frames


# ------------------------------------------------------------------ conversion


async def convert_version(ctx: dict, job_id: str) -> dict:
    """R1.10 - export/quantise and land the artifact as a new draft version."""
    async with SessionLocal() as session:
        job = await _start(session, job_id)
        if job is None:
            return {"skipped": True}
        params = job.params or {}
        try:
            source = await session.get(ModelVersion, params["version_id"])
            if source is not None:
                params = {**params, "artifact": source.artifact_info or {},
                          "config": source.config or {}}
            model = await session.get(Model, params["model_id"])
            if not (source and model and source.weights_key):
                raise ApiError("not_found", "the source version is missing its weights")

            target = params["target"]
            with tempfile.TemporaryDirectory(prefix="aperture-convert-") as tmp:
                local = os.path.join(tmp, os.path.basename(source.weights_key))
                await storage.download_to_path(settings.s3_bucket_weights, source.weights_key,
                                               local)
                converted = _convert_artifact(local, tmp, target, params)
                produced, fmt, notes = converted[0], converted[1], converted[2]
                config_override = converted[3] if len(converted) > 3 else None

                with open(produced, "rb") as fh:
                    data = fh.read()

            number = await next_version_number(session, model.id)  # locks the model until commit
            filename = os.path.basename(produced)
            key = storage.weights_key(model.storage_prefix, number, filename)
            info = await storage.put_bytes(settings.s3_bucket_weights, key, data)

            # a converted RF-DETR brings its own config, read from the weights;
            # everything else keeps the source version's
            config = dict(config_override or source.config or {})
            config["backend"] = "tensorrt" if fmt == "tensorrt" else "onnx"
            # A rebuilt RF-DETR config names its classes class_0..class_N, which
            # would throw away real names the source version already had. Carry
            # them over, re-aligned to whatever dead leading column this export
            # turned out to have.
            config["classes"] = _carry_class_names(source.config or {}, config)
            if target.endswith("fp16"):
                config.setdefault("input", {})["dtype"] = "float32"

            new_version = ModelVersion(
                model_id=model.id, version=number, status="draft", config=config,
                preset=source.preset, artifact_format=fmt, weights_key=key,
                weights_bytes=len(data), weights_sha256=info.sha256,
                training_run_id=source.training_run_id, dataset_version=source.dataset_version,
                metrics=dict(source.metrics or {}), git_commit=source.git_commit,
                changelog=(params.get("changelog")
                           or f"{target} conversion of v{source.version}. {notes}"),
                source="conversion", parent_version_id=source.id, created_by=job.created_by,
            )
            session.add(new_version)
            await session.flush()
            await _finish(session, job, result={
                "new_version": number, "version_id": new_version.id, "bytes": len(data),
                "artifact_format": fmt, "notes": notes,
                "next_step": "validate the new version before deploying it",
            })
            return {"new_version": number}
        except Exception as exc:
            log.exception("conversion failed", extra={"job_id": job_id})
            await _finish(session, job, error=str(exc)[:2000])
            return {"error": str(exc)}


def _rfdetr_parameters(state: dict) -> dict:
    """Recover the training geometry from the weights themselves.

    The reading lives in `aperture_core.rfdetr` because the runtime needs the
    same numbers to serve a `.pt` directly - one reader, so an export and a
    direct load can never disagree about what the checkpoint is.
    """
    return geometry_from_state_dict(state)


def _convert_rfdetr(source_path: str, workdir: str, params: dict) -> tuple[str, str, str, dict]:
    """Rebuild an RF-DETR checkpoint with its own package and export ONNX."""
    try:
        import rfdetr  # noqa: F401  - imported by `rfdetr_build`; checked here for the message
        import torch
    except ImportError as exc:
        raise RuntimeError(
            "converting RF-DETR weights needs the rfdetr package in the converter image "
            "(docker compose --profile convert up -d converter)"
        ) from exc

    state = torch.load(source_path, map_location="cpu", weights_only=False)
    detected = _rfdetr_parameters(state)
    log.info("recovered rf-detr geometry from the weights", extra=detected)

    # `build` rebuilds and then *checks* the load: a variant that leaves
    # checkpoint tensors unused is rejected rather than exported. Exporting a
    # partial load is the worst of the failure modes - the ONNX serves, and only
    # its scores say anything is wrong.
    model, used, geometry = rfdetr_build(source_path, device="cpu", detected=detected)
    detected = geometry
    kwargs = rebuild_kwargs(geometry)

    model.export(output_dir=workdir, opset_version=params.get("opset", 17))
    produced = [name for name in os.listdir(workdir) if name.endswith(".onnx")]
    if not produced:
        raise RuntimeError(f"{used} exported no ONNX file")
    out = os.path.join(workdir, produced[0])

    offset = _onnx_class_offset(out, detected.get("head_width", kwargs["num_classes"] + 1))
    config = _rfdetr_config(detected, offset)
    note = (f"{used} rebuilt from the weights (patch {kwargs['patch_size']}, "
            f"resolution {kwargs['resolution']}, {kwargs['num_classes']} classes) "
            f"and exported with opset {params.get('opset', 17)}")
    if offset:
        note += (f"; the exporter prepended {offset} dead logit column(s), so the config "
                 f"carries class_offset={offset} to keep class ids aligned")
    return out, "onnx", note, config


def _onnx_class_offset(onnx_path: str, head_width: int) -> int:
    """How many leading logit columns of an export are not classes.

    RF-DETR's ONNX exporter widens the classification head by one and puts the
    new column first: zero weights and a bias around -20, so it can never fire.
    Read as-is that column shifts every class id up by one, which is invisible
    while the names are placeholders and quietly mislabels everything once the
    real ones are filled in.

    Rather than assume the convention, this measures it: a leading column is
    skipped only when its bias sits far below every trained one.

    The width comparison alone is not enough. A checkpoint trained through
    Roboflow already carries that dead column, so the export widens nothing
    (7 -> 7, 116 -> 116) and a delta-only test concludes there is nothing to
    skip - while the column is still there, still dead, and still shifting every
    id. So the bias is inspected whenever the export is at least as wide as the
    checkpoint, and `delta` only decides how many columns to consider.
    """
    try:
        import onnx
        from onnx import numpy_helper
    except Exception:
        return 0
    try:
        graph = onnx.load(onnx_path, load_external_data=False).graph
    except Exception as exc:
        log.warning("could not inspect the exported class head", extra={"error": str(exc)[:160]})
        return 0

    for init in graph.initializer:
        if not init.name.endswith("class_embed.bias"):
            continue
        bias = numpy_helper.to_array(init)
        if bias.shape[0] < head_width:
            return 0
        # however many columns the export added, plus the one the checkpoint may
        # already have carried - at least one column is always worth testing
        delta = max(1, int(bias.shape[0]) - head_width)
        if delta >= bias.shape[0]:
            return 0
        leading = bias[:delta]
        rest = bias[delta:]
        # a slot that never fires sits far below every trained one
        if float(leading.max()) < min(-10.0, float(rest.min()) - 5.0):
            return delta
        return 0
    return 0


def _rfdetr_config(detected: dict, class_offset: int = 0) -> dict:
    """A config that matches what the exporter just produced.

    The point of doing this here is that the new version validates on the first
    try: the resolution, the decoder and the class count all come from the same
    place as the graph.
    """
    from aperture_core import preset_config

    preset = "rf-detr-seg" if detected["segmentation"] else "rf-detr"
    config = preset_config(preset).model_dump(mode="json")
    resolution = detected["resolution"]
    config["input"]["width"] = config["input"]["height"] = resolution
    config["preprocess"]["resize"]["width"] = resolution
    config["preprocess"]["resize"]["height"] = resolution
    count = detected["num_classes"]
    # The checkpoint carries no names, only a count. Placeholders keep the ids
    # aligned with the head, and the UI asks for the real names.
    config["classes"] = [f"class_{index}" for index in range(count)]
    config["postprocess"]["class_offset"] = class_offset
    config["notes"] = (f"Converted from RF-DETR weights: patch {detected['patch_size']}, "
                       f"resolution {resolution}, {count} classes. Replace the placeholder "
                       "class names with your dataset's labels.")
    if class_offset:
        config["notes"] += (f" The export prepends {class_offset} dead logit column(s); "
                            f"class_offset={class_offset} skips them so ids line up with "
                            "the class list.")
    return config


def _joined(*notes: str) -> str:
    """One sentence per step that actually happened."""
    return "; ".join(note for note in notes if note and "copied unchanged" not in note)


def _to_onnx(source_path: str, workdir: str, params: dict):
    """Produce an ONNX graph from whatever was uploaded.

    Quantisation and engine builds all read ONNX, so they go through here first
    rather than each assuming the upload already was one - handing a torch
    checkpoint to a protobuf parser produces "Unable to parse proto from file",
    which says nothing about what went wrong or what to do about it.
    """
    base = os.path.splitext(os.path.basename(source_path))[0]

    if source_path.endswith(".onnx"):
        return source_path, "onnx", "already ONNX; copied unchanged", None

    artifact = params.get("artifact") or {}
    usability = artifact.get("usability")

    if usability in ("weights_only", "unknown"):
        # This is the honest answer, not a limitation to work around: a
        # state_dict carries no graph, so there is nothing to trace.
        raise RuntimeError(
            (artifact.get("detail") or "this artifact carries no model graph")
            + " " + (artifact.get("next_step") or
                     "Export ONNX from the code that defines the model.")
        )

    if (artifact.get("family") == "rf-detr"
            and artifact.get("kind") == "state_dict"):
        return _convert_rfdetr(source_path, workdir, params)

    if artifact.get("kind") == "ultralytics":
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise RuntimeError(
                "converting an Ultralytics checkpoint needs the ultralytics package in "
                "the worker image (docker compose --profile convert up), or run "
                "YOLO('weights.pt').export(format='onnx') yourself and upload the .onnx"
            ) from exc
        shape = (params.get("config") or {}).get("input") or {}
        exported = YOLO(source_path).export(
            format="onnx", dynamic=True, simplify=True,
            opset=params.get("opset", 17),
            imgsz=(shape.get("height", 640), shape.get("width", 640)),
        )
        return str(exported), "onnx", ("ultralytics export, dynamic batch, opset "
                                       f"{params.get('opset', 17)}"), None

    try:
        import torch
    except ImportError as exc:
        raise RuntimeError(
            "exporting a TorchScript archive to ONNX needs torch in the worker image "
            "(docker compose --profile convert up), or export on your training machine "
            "and upload the .onnx"
        ) from exc
    module = torch.jit.load(source_path, map_location="cpu")
    module.eval()
    shape = (params.get("config") or {}).get("input") or {}
    dummy = torch.zeros(1, shape.get("channels", 3), shape.get("height", 640),
                        shape.get("width", 640))
    out = os.path.join(workdir, f"{base}.onnx")
    torch.onnx.export(module, dummy, out, opset_version=params.get("opset", 17),
                      input_names=["images"], output_names=["output0"],
                      dynamic_axes={"images": {0: "batch"}, "output0": {0: "batch"}})
    return out, "onnx", f"torch.onnx.export, opset {params.get('opset', 17)}", None


def _placeholder_names(names: list) -> bool:
    """Names a conversion invented, as opposed to a dataset's own."""
    return bool(names) and all(re.fullmatch(r"(class_)?\d+", str(n)) for n in names)


def _carry_class_names(source_config: dict, new_config: dict) -> list:
    """The source version's class names, aligned to the converted head.

    A conversion changes how many leading logit columns the decoder skips, so the
    names cannot simply be copied: `class_offset` says where the reported ids now
    start, and the list has to start there too. When the counts do not work out,
    the converted config keeps its own placeholders rather than shipping names
    that are one out - a wrong name is worse than an obvious one.
    """
    source_names = list(source_config.get("classes") or [])
    target_names = list(new_config.get("classes") or [])
    if not source_names or _placeholder_names(source_names):
        return target_names
    if not target_names or not _placeholder_names(target_names):
        return target_names or source_names

    source_offset = int((source_config.get("postprocess") or {}).get("class_offset", 0) or 0)
    target_offset = int((new_config.get("postprocess") or {}).get("class_offset", 0) or 0)
    # back to the full label space, then forward to where this export starts
    full = ([None] * source_offset) + source_names
    aligned = full[target_offset:]
    if len(aligned) != len(target_names) or any(n is None for n in aligned):
        log.warning("could not carry class names across the conversion; keeping placeholders",
                    extra={"source": len(source_names), "target": len(target_names),
                           "source_offset": source_offset, "target_offset": target_offset})
        return target_names
    return aligned


def _convert_artifact(source_path: str, workdir: str, target: str,
                      params: dict) -> tuple:
    """Best-effort conversion with whatever tooling the worker image carries."""
    base = os.path.splitext(os.path.basename(source_path))[0]

    if target == "onnx":
        return _to_onnx(source_path, workdir, params)

    if target == "onnx_fp16":
        try:
            import onnx
            from onnxconverter_common import float16
        except ImportError as exc:
            raise RuntimeError(
                "FP16 conversion needs onnx and onnxconverter-common in the worker image"
            ) from exc
        graph_path, _, exported_note, config = _to_onnx(source_path, workdir, params)
        base = os.path.splitext(os.path.basename(graph_path))[0]
        model = onnx.load(graph_path)
        converted = float16.convert_float_to_float16(model, keep_io_types=True)
        out = os.path.join(workdir, f"{base}-fp16.onnx")
        onnx.save(converted, out)
        return out, "onnx", _joined(exported_note,
                                    "graph weights cast to FP16, IO kept in FP32"), config

    if target == "onnx_int8":
        try:
            from onnxruntime.quantization import QuantType, quantize_dynamic
        except ImportError as exc:
            raise RuntimeError(
                "INT8 quantisation needs onnxruntime's quantization extras in the worker image"
            ) from exc
        graph_path, _, exported_note, config = _to_onnx(source_path, workdir, params)
        base = os.path.splitext(os.path.basename(graph_path))[0]
        out = os.path.join(workdir, f"{base}-int8.onnx")
        quantize_dynamic(graph_path, out, weight_type=QuantType.QInt8)
        return out, "onnx", _joined(exported_note,
                                    "dynamic INT8 quantisation (weights only). Check accuracy on "
                                    "your own images before deploying - detection heads are "
                                    "sensitive to this"), config

    if target.startswith("tensorrt"):
        graph_path, _, exported_note, config = _to_onnx(source_path, workdir, params)
        base = os.path.splitext(os.path.basename(graph_path))[0]
        precision = "--fp16" if target.endswith("fp16") else "--int8"
        out = os.path.join(workdir, f"{base}.engine")
        command = ["trtexec", f"--onnx={graph_path}", f"--saveEngine={out}", precision,
                   f"--memPoolSize=workspace:{params.get('workspace_mb', 4096)}"]
        result = subprocess.run(command, capture_output=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(
                "trtexec is not available in this worker image, or the build failed. TensorRT "
                "engines are GPU- and driver-specific, so build them on the GPU runtime image: "
                + result.stderr.decode("utf-8", "replace")[-400:]
            )
        return out, "tensorrt", _joined(exported_note,
                                        f"trtexec {precision.strip('-')} engine"), config

    raise RuntimeError(f"unknown conversion target '{target}'")


# ---------------------------------------------------------------- data exports


def _in_workspace(workspace_id: str):
    """Inference logs of the models in one workspace; started from the API, a job stays there."""
    return InferenceLog.model_id.in_(
        sa.select(Model.id).where(Model.workspace_id == workspace_id)
    )


async def export_flagged_dataset(ctx: dict, job_id: str) -> dict:
    """R5.10 - flagged predictions out as a dataset you can retrain on."""
    async with SessionLocal() as session:
        job = await _start(session, job_id)
        if job is None:
            return {"skipped": True}
        params = job.params or {}
        try:
            since = datetime.now(UTC) - timedelta(hours=int(params.get("since_hours", 2160)))
            clauses = [InferenceLog.flagged.is_(True),
                       InferenceLog.created_at >= since]
            if params.get("model_id"):
                clauses.append(InferenceLog.model_id == params["model_id"])
            if params.get("workspace_id"):
                clauses.append(_in_workspace(params["workspace_id"]))
            rows = list(
                (
                    await session.execute(
                        sa.select(InferenceLog).where(*clauses)
                        .order_by(InferenceLog.created_at.desc()).limit(20_000)
                    )
                ).scalars().all()
            )
            job.total = len(rows)
            await session.commit()

            fmt = params.get("fmt", "coco")
            buffer = io.BytesIO()
            manifest: list[dict] = []
            categories: dict[str, int] = {}
            coco_images, coco_annotations = [], []
            annotation_id = 1

            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                for index, row in enumerate(rows):
                    images = (row.capture or {}).get("images") or []
                    if not images:
                        continue
                    predictions = ((row.output or {}).get("results") or [{}])[0]
                    for image_index, item in enumerate(images):
                        try:
                            data = await storage.get_bytes(settings.s3_bucket_captures,
                                                           item["key"])
                        except Exception:
                            continue
                        name = f"images/{row.request_id}_{image_index}.jpg"
                        archive.writestr(name, data)
                        detections = predictions.get("detections") or []
                        manifest.append({
                            "file": name, "request_id": row.request_id,
                            "model": row.model_slug, "version": row.version_number,
                            "flag_label": row.flag_label, "flag_note": row.flag_note,
                            "predictions": detections,
                        })
                        width = predictions.get("width") or 0
                        height = predictions.get("height") or 0
                        image_id = len(coco_images) + 1
                        coco_images.append({"id": image_id, "file_name": name, "width": width,
                                            "height": height})
                        yolo_lines = []
                        for detection in detections:
                            label = detection.get("class_name", "object")
                            categories.setdefault(label, len(categories) + 1)
                            box = detection.get("box") or [0, 0, 0, 0]
                            bw, bh = box[2] - box[0], box[3] - box[1]
                            coco_annotations.append({
                                "id": annotation_id, "image_id": image_id,
                                "category_id": categories[label],
                                "bbox": [box[0], box[1], bw, bh], "area": bw * bh,
                                "iscrowd": 0, "score": detection.get("score"),
                            })
                            annotation_id += 1
                            if width and height:
                                yolo_lines.append(
                                    f"{categories[label] - 1} "
                                    f"{((box[0] + box[2]) / 2) / width:.6f} "
                                    f"{((box[1] + box[3]) / 2) / height:.6f} "
                                    f"{bw / width:.6f} {bh / height:.6f}"
                                )
                        if fmt == "yolo":
                            archive.writestr(name.replace("images/", "labels/")
                                             .rsplit(".", 1)[0] + ".txt",
                                             "\n".join(yolo_lines))
                    job.progress = index + 1
                    if index % 25 == 0:
                        await session.commit()

                archive.writestr("manifest.jsonl",
                                 "\n".join(json.dumps(m, default=str) for m in manifest))
                if fmt == "coco":
                    archive.writestr("annotations.json", json.dumps({
                        "info": {"description": "Aperture flagged predictions export",
                                 "generated_at": datetime.now(UTC).isoformat(),
                                 "note": "boxes are model predictions, not verified labels - "
                                         "correct them before training"},
                        "images": coco_images, "annotations": coco_annotations,
                        "categories": [{"id": v, "name": k} for k, v in categories.items()],
                    }, default=str))
                if fmt == "yolo":
                    archive.writestr("classes.txt",
                                     "\n".join(k for k, _ in sorted(categories.items(),
                                                                    key=lambda kv: kv[1])))

            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            key = f"exports/flagged-{stamp}.zip"
            await storage.put_bytes(settings.s3_bucket_reports, key, buffer.getvalue(),
                                    content_type="application/zip")
            await _finish(session, job, result={
                "artifact_key": key, "images": len(manifest), "records": len(rows),
                "format": fmt, "categories": list(categories),
            })
            return {"images": len(manifest)}
        except Exception as exc:
            log.exception("flagged export failed", extra={"job_id": job_id})
            await _finish(session, job, error=str(exc)[:2000])
            return {"error": str(exc)}


# ------------------------------------------------------------------- retention


async def purge_captures(ctx: dict, job_id: str) -> dict:
    """R7.6 - hard delete on request."""
    async with SessionLocal() as session:
        job = await _start(session, job_id)
        if job is None:
            return {"skipped": True}
        params = job.params or {}
        try:
            clauses = []
            before_hours = int(params.get("before_hours") or 0)
            if before_hours:
                clauses.append(
                    InferenceLog.created_at <= datetime.now(UTC) - timedelta(hours=before_hours)
                )
            else:
                clauses.append(InferenceLog.purge_after <= datetime.now(UTC))
            if params.get("model_id"):
                clauses.append(InferenceLog.model_id == params["model_id"])
            if params.get("workspace_id"):
                clauses.append(_in_workspace(params["workspace_id"]))

            rows = list(
                (
                    await session.execute(
                        sa.select(InferenceLog).where(*clauses).limit(50_000)
                    )
                ).scalars().all()
            )
            deleted = 0
            for row in rows:
                for item in ((row.capture or {}).get("images") or []) + \
                            ((row.capture or {}).get("overlays") or []):
                    try:
                        await storage.delete_object(settings.s3_bucket_captures, item["key"])
                        deleted += 1
                    except Exception:
                        log.warning("could not delete capture", extra={"key": item.get("key")})
                row.capture = {"purged_at": datetime.now(UTC).isoformat()}
                row.output = {"purged": True}
                row.input_ref = {k: v for k, v in (row.input_ref or {}).items()
                                 if k in ("count", "sources")}
            await session.commit()
            await _finish(session, job, result={"records": len(rows), "objects_deleted": deleted})
            return {"objects_deleted": deleted}
        except Exception as exc:
            log.exception("purge failed", extra={"job_id": job_id})
            await _finish(session, job, error=str(exc)[:2000])
            return {"error": str(exc)}


async def retention_sweep(ctx: dict) -> dict:
    """Cron: expire captures whose purge_after has passed, and stale upload sessions."""
    async with SessionLocal() as session:
        now = datetime.now(UTC)
        rows = list(
            (
                await session.execute(
                    sa.select(InferenceLog).where(
                        InferenceLog.purge_after.is_not(None),
                        InferenceLog.purge_after <= now,
                        InferenceLog.flagged.is_(False),
                    ).limit(5_000)
                )
            ).scalars().all()
        )
        deleted = 0
        for row in rows:
            for item in ((row.capture or {}).get("images") or []) + \
                        ((row.capture or {}).get("overlays") or []):
                try:
                    await storage.delete_object(settings.s3_bucket_captures, item["key"])
                    deleted += 1
                except Exception:
                    pass
            row.capture = {"expired_at": now.isoformat()}
            row.purge_after = None
        stale = list(
            (
                await session.execute(
                    sa.select(UploadSession).where(UploadSession.status == "open",
                                                   UploadSession.expires_at <= now)
                )
            ).scalars().all()
        )
        for upload in stale:
            if upload.multipart_id:
                await storage.abort_multipart(settings.s3_bucket_weights, upload.key,
                                              upload.multipart_id)
            upload.status = "expired"
        # Every rebuilt container joins under a new id, so without this the fleet
        # view slowly fills with runtimes that no longer exist.
        gone = (
            await session.execute(
                sa.delete(Runtime).where(
                    sa.or_(Runtime.last_heartbeat.is_(None),
                           Runtime.last_heartbeat <= now - timedelta(hours=2)),
                    sa.not_(sa.exists().where(Assignment.runtime_id == Runtime.id)),
                )
            )
        ).rowcount or 0

        await session.commit()
        if deleted or stale or gone:
            log.info("retention sweep", extra={"objects_deleted": deleted,
                                               "uploads_expired": len(stale),
                                               "runtimes_pruned": gone})
        return {"objects_deleted": deleted, "uploads_expired": len(stale),
                "runtimes_pruned": gone}


# ------------------------------------------------------------------ cron ticks


async def heartbeat_tick(ctx: dict) -> dict:
    """Liveness: proves the cron scheduler itself is still running."""
    from app.jobs.heartbeat import WORKER_FILE, touch

    touch(WORKER_FILE)
    return {"ok": True}


async def converter_heartbeat_tick(ctx: dict) -> dict:
    """The converter runs no other cron, so it needs its own liveness tick."""
    from app.jobs.heartbeat import CONVERTER_FILE, touch

    touch(CONVERTER_FILE)
    return {"ok": True}


async def reconcile_tick(ctx: dict) -> dict:
    async with SessionLocal() as session:
        return await reconcile(session)


async def autoscale_tick(ctx: dict) -> dict:
    async with SessionLocal() as session:
        changes = await autoscale(session)
        if changes:
            log.info("autoscaled", extra={"changes": changes})
        return {"changes": changes}


async def evaluate_alerts(ctx: dict) -> dict:
    async with SessionLocal() as session:
        fired = await alert_service.evaluate_all(session)
        return {"fired": fired}


async def drift_rollup(ctx: dict) -> dict:
    async with SessionLocal() as session:
        hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
        written = await drift_service.rollup_hour(session, hour_start=hour)
        return {"windows": written, "hour": hour.isoformat()}


async def run_report_schedule(ctx: dict, schedule_id: str) -> dict:
    async with SessionLocal() as session:
        schedule = await session.get(ReportSchedule, schedule_id)
        if schedule is None:
            return {"skipped": True}
        params = schedule.params or {}
        report = await report_service.build(
            session, kind=schedule.kind,
            group_by=params.get("group_by", "model"),
            since_hours=int(params.get("since_hours", 168)),
            model_id=params.get("model_id"),
        )
        key = await report_service.store_artifact(report, schedule.fmt)
        schedule.last_run_at = datetime.now(UTC)
        schedule.last_artifact_key = key
        await session.commit()

        if schedule.channel != "none" and schedule.destination:
            url = await storage.presign_get(settings.s3_bucket_reports, key, 86400, public=True)
            await _deliver_report(schedule, report, url)
        return {"artifact_key": key, "rows": len(report["rows"])}


async def _deliver_report(schedule: ReportSchedule, report: dict, url: str) -> None:
    line = (f"{report['title']} for {report['instance']} - {len(report['rows'])} rows, "
            f"window {report['window_hours']} h")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            if schedule.channel == "slack":
                await client.post(schedule.destination,
                                  json={"text": f"*{line}*\n<{url}|Download {schedule.fmt.upper()}>"})
            elif schedule.channel == "webhook":
                await client.post(schedule.destination, json={
                    "report": report["kind"], "instance": report["instance"],
                    "rows": len(report["rows"]), "totals": report["totals"], "url": url,
                })
            elif schedule.channel == "email":
                alert_service._send_email(
                    schedule.destination, f"[Aperture] {line}", f"{line}\n\n{url}", {}
                )
    except Exception:
        log.warning("report delivery failed", extra={"schedule": schedule.name}, exc_info=True)


async def report_scheduler(ctx: dict) -> dict:
    """Minute tick: fire any schedule whose cron matches this minute."""
    async with SessionLocal() as session:
        now = datetime.now(UTC)
        rows = list(
            (
                await session.execute(
                    sa.select(ReportSchedule).where(ReportSchedule.enabled.is_(True),
                                                    ReportSchedule.cron.is_not(None))
                )
            ).scalars().all()
        )
        fired = []
        for schedule in rows:
            if not _cron_matches(schedule.cron or "", now):
                continue
            if schedule.last_run_at and (now - schedule.last_run_at).total_seconds() < 90:
                continue
            from app.jobs.queue import enqueue

            await enqueue("run_report_schedule", schedule.id)
            fired.append(schedule.name)
        return {"fired": fired}


def _cron_matches(expression: str, when: datetime) -> bool:
    fields = expression.split()
    if len(fields) != 5:
        return False
    values = [when.minute, when.hour, when.day, when.month, when.isoweekday() % 7]
    for field, value in zip(fields, values):
        if not _field_matches(field, value):
            return False
    return True


def _field_matches(field: str, value: int) -> bool:
    for part in field.split(","):
        if part == "*":
            return True
        if part.startswith("*/"):
            step = int(part[2:] or 1)
            if step and value % step == 0:
                return True
        elif "-" in part:
            low, _, high = part.partition("-")
            if int(low) <= value <= int(high):
                return True
        elif part.isdigit() and int(part) == value:
            return True
    return False


# ------------------------------------------------------- roboflow import


ROBOFLOW_API = "https://api.roboflow.com"
# Roboflow's model type -> the Aperture preset that decodes it.
ROBOFLOW_PRESETS = {
    "rfdetr-seg": "rf-detr-seg", "rfdetr-seg-2xlarge": "rf-detr-seg",
    "rfdetr-seg-preview": "rf-detr-seg", "rfdetr-base": "rf-detr", "rfdetr": "rf-detr",
    "yolov8-seg": "yolov8-seg", "yolo11-seg": "yolov8-seg", "yolov11-seg": "yolov8-seg",
    "yolov8": "yolov8", "yolo11": "yolov8", "yolov11": "yolov8", "yolov5": "yolov5",
    "yolov8-pose": "yolov8-pose", "yolov8-cls": "resnet-imagenet",
}


def _roboflow_preset(model_type: str | None) -> str | None:
    """The preset for a Roboflow model type, ignoring a YOLO size letter ('yolov11l-seg')."""
    model_type = model_type or ""
    return ROBOFLOW_PRESETS.get(model_type) or ROBOFLOW_PRESETS.get(
        re.sub(r"^(yolov?\d+)[nsmlx](?=-|$)", r"\1", model_type))


# Roboflow names its background class `background_class<digits>` and always puts
# it first. It is the column RF-DETR's export leaves dead.
BACKGROUND_SENTINEL = "background_class"


def parse_roboflow_ref(ref: str) -> tuple[str, str, int]:
    """workspace, project, version - from a link or a bare `ws/project/version`.

    Accepts what people actually have in hand: the app URL they were just looking
    at, the `model_id` a workflow names, or the three parts typed by hand.
    """
    cleaned = (ref or "").strip()
    for prefix in ("https://", "http://"):
        cleaned = cleaned.removeprefix(prefix)
    cleaned = re.sub(r"^(app|universe|detect)\.roboflow\.com/", "", cleaned)
    cleaned = cleaned.split("?", 1)[0].strip("/")
    parts = [p for p in cleaned.split("/") if p]
    # The app has several shapes for the same model, and people paste whichever
    # page they were on:
    #   ws/project/13                       the version
    #   ws/project/models/project/13        the trained-model page
    #   ws/project/13/images                a version's images
    # The workspace and project are always the first two segments, and the
    # version is the last bare number - so take it from the end.
    if len(parts) >= 3:
        for candidate in reversed(parts[2:]):
            if candidate.isdigit():
                return parts[0], parts[1], int(candidate)
    raise ApiError(
        "invalid_request",
        "expected a Roboflow model reference like "
        "'workspace/project/version' or https://app.roboflow.com/workspace/project/version, "
        f"got '{ref}'",
    )


def _roboflow_classes(payload: dict) -> list[str]:
    """A version's class list, in label-space order - nothing removed.

    Position is the class id, so this returns the list exactly as Roboflow
    orders it, including `background_class…` at the front and `__ignore__` at the
    back. Both occupy real columns of the head.

    Nothing is trimmed here because nothing needs to be: these names go on the
    checkpoint as served, whose head *is* the label space. Only a later export
    can introduce a dead leading column, and `_carry_class_names` re-aligns them
    against the offset measured on that export.
    """
    classes = (payload.get("version") or {}).get("classes")
    if isinstance(classes, dict):      # older payloads: {name: count}, unordered
        raise ApiError(
            "invalid_config",
            "this Roboflow version reports its classes unordered, so their ids cannot be "
            "recovered; import the weights manually and set the class list by hand",
        )
    if not isinstance(classes, list) or not classes:
        raise ApiError("not_found", "this Roboflow version reports no classes")
    return [str(c) for c in classes]


async def _roboflow_served_classes(client: httpx.AsyncClient, project_id: str, version: int,
                                   api_key: str) -> list:
    """The class list Roboflow serves a version's weights with, when its metadata lists none.

    Newer versions answer `classes: []` on the version endpoint while the model
    itself still carries its ordered list - the one inference reads.
    """
    resp = await client.get(f"{ROBOFLOW_API}/ort/{project_id}/{version}", params={"api_key": api_key})
    classes = (resp.json().get("ort") or {}).get("classes") if resp.status_code < 400 else None
    return classes if isinstance(classes, list) else []


def _artifact_format_for(filename: str) -> str:
    suffix = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    return {".onnx": "onnx", ".pt": "pt", ".pth": "pt", ".torchscript": "torchscript",
            ".safetensors": "safetensors", ".engine": "tensorrt",
            ".plan": "tensorrt"}.get(suffix, "other")


async def import_roboflow(ctx: dict, job_id: str, api_key: str | None = None) -> dict:
    """Download a trained Roboflow model and land it as a ready Aperture version.

    This is the manual import written down: read the version's ordered class list,
    pull its weights, convert them to ONNX, and write the names and the class
    offset the conversion measured. Doing it by hand is where the mistakes live -
    a class list from the wrong version, or a dead leading logit column left
    unaccounted for, both mislabel every prediction while looking healthy.

    `api_key` is the caller's own Roboflow key, passed with the task instead of
    stored on the job. Jobs queued before that change still carry it in params.
    """
    async with SessionLocal() as session:
        job = await _start(session, job_id)
        if job is None:
            return {"skipped": True}
        params = job.params or {}
        try:
            workspace, project_id, version = (
                params["workspace"], params["project"], int(params["version"]))
            from aperture_core.artifacts import inspect_artifact

            api_key = api_key or params.get("api_key") or settings.roboflow_api_key
            if not api_key:
                raise ApiError(
                    "invalid_config",
                    "no Roboflow API key: set AP_ROBOFLOW_API_KEY or send one with the import",
                )
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.get(
                    f"{ROBOFLOW_API}/{workspace}/{project_id}/{version}",
                    params={"api_key": api_key})
                if resp.status_code >= 400:
                    raise ApiError(
                        "not_found",
                        f"Roboflow refused {workspace}/{project_id}/{version}: "
                        f"HTTP {resp.status_code}. Check the reference and the API key.",
                    )
                payload = resp.json()
                detail = payload.get("version") or {}
                if isinstance(detail, dict) and detail.get("classes") == []:
                    detail["classes"] = await _roboflow_served_classes(
                        client, project_id, version, api_key)
            classes = _roboflow_classes(payload)
            detail = payload.get("version") or {}

            slug = params.get("slug") or re.sub(r"[^a-z0-9._-]+", "-", project_id.lower()).strip("-")
            target = await session.get(Workspace, params.get("workspace_id") or "")
            if target is None:
                raise ApiError("not_found", "the workspace this import was started in is gone")
            model = (
                await session.execute(
                    sa.select(Model).where(Model.workspace_id == target.id, Model.slug == slug)
                )
            ).scalar_one_or_none()

            with tempfile.TemporaryDirectory(prefix="aperture-rfimport-") as tmp:
                weights, model_type = _roboflow_download(
                    workspace, project_id, version, api_key, tmp)
                preset = params.get("preset") or _roboflow_preset(model_type)
                if not preset:
                    raise ApiError(
                        "invalid_config",
                        f"no Aperture preset is mapped to the Roboflow model type "
                        f"'{model_type}'; pass `preset` explicitly",
                        details={"known": sorted(set(ROBOFLOW_PRESETS.values()))},
                    )
                task = "segmentation" if preset.endswith("-seg") else (
                    "classification" if preset in ("resnet-imagenet",) else
                    "keypoints" if preset.endswith("-pose") else "detection")

                if model is None:
                    model = Model(
                        workspace_id=target.id, slug=slug,
                        name=detail.get("name") or project_id, task=task,
                        framework="pytorch", owner=job.created_by or "",
                        description=f"Imported from Roboflow {workspace}/{project_id}/{version}.",
                        labels=["roboflow-import"],
                    )
                    session.add(model)
                    await session.flush()

                # The bytes Roboflow served are what gets stored - not a
                # re-export of them. A version records the artifact it was given,
                # so its sha256 is the one you can check against Roboflow, and
                # any ONNX is a separate version with this one as its parent.
                filename = os.path.basename(weights)
                with open(weights, "rb") as fh:
                    data = fh.read()
                artifact = inspect_artifact(weights, filename=filename)

            number = await next_version_number(session, model.id)  # locks the model until commit
            key = storage.weights_key(model.storage_prefix, number, filename)
            info = await storage.put_bytes(settings.s3_bucket_weights, key, data)

            from aperture_core import preset_config

            config = preset_config(preset).model_dump(mode="json")
            # The checkpoint's head is the label space itself, so the names go in
            # as Roboflow orders them. A conversion may skip a dead leading
            # column later; `_carry_class_names` re-aligns them if it does.
            config["classes"] = classes
            config["backend"] = "torch" if artifact.kind != "onnx" else "onnx"

            new_version = ModelVersion(
                model_id=model.id, version=number, status="draft", config=config,
                preset=preset, artifact_format=_artifact_format_for(filename),
                weights_key=key, weights_bytes=len(data), weights_sha256=info.sha256,
                artifact_info=artifact.as_dict(),
                dataset_version=f"{project_id}/{version}",
                changelog=(params.get("changelog")
                           or f"Imported from Roboflow {workspace}/{project_id}/{version}"
                              f" ({model_type}), as served."),
                source="roboflow", created_by=job.created_by,
            )
            session.add(new_version)
            await session.flush()

            # Converting is still a separate version with this one as its parent,
            # so the downloaded artifact stays on record either way. An RF-DETR
            # state_dict is servable now, but only on a runtime carrying rfdetr -
            # so the export still runs, and the import lands with both.
            convert_job = None
            follow_on = None
            needs_export = not artifact.servable or artifact.kind == "state_dict"
            if params.get("convert", True) and needs_export:
                convert_job = Job(
                    kind="convert", status="queued",
                    created_by=job.created_by,
                    params={"model_id": model.id, "version_id": new_version.id,
                            "target": "onnx", "opset": params.get("opset", 17),
                            "changelog": f"ONNX export of the Roboflow import "
                                         f"({project_id}/{version})."},
                )
                session.add(convert_job)
                await session.flush()
                follow_on = convert_job.id

            await _finish(session, job, result={
                "model": model.slug, "new_version": number, "version_id": new_version.id,
                "preset": preset, "classes": len(classes), "bytes": len(data),
                "artifact_format": new_version.artifact_format,
                "weights_sha256": info.sha256,
                "roboflow": f"{workspace}/{project_id}/{version}",
                "convert_job": follow_on,
                "next_step": ("the weights are stored as served; an ONNX export is running "
                              "as a separate version" if follow_on else
                              "publish the version, then deploy it"),
            })
            if convert_job is not None:
                # only now is the follow-on row committed and visible to the converter
                from app.jobs.queue import QUEUE_CONVERT, dispatch

                await dispatch(session, convert_job, "convert_version", queue=QUEUE_CONVERT)
            return {"model": model.slug, "new_version": number}
        except Exception as exc:
            log.exception("roboflow import failed", extra={"job_id": job_id})
            await _finish(session, job, error=str(exc)[:2000])
            return {"error": str(exc)}


def _roboflow_download(workspace: str, project_id: str, version: int,
                       api_key: str, out_dir: str) -> tuple[str, str | None]:
    """The version's trained weights on disk, plus Roboflow's model type.

    `version.model` is deprecated and returns None on current Roboflow projects, so this
    uses `models()`. A version that was never trained, or whose architecture
    Roboflow only hosts, has nothing to hand over - which is worth saying plainly
    rather than failing later on a missing file.
    """
    try:
        from roboflow import Roboflow
    except ImportError as exc:
        raise ApiError(
            "invalid_config",
            "importing from Roboflow needs the roboflow package in the converter image "
            "(docker compose --profile convert up -d converter)",
        ) from exc

    rf = Roboflow(api_key=api_key)
    ver = rf.workspace(workspace).project(project_id).version(version)
    trained = list(ver.models() or [])
    if not trained:
        raise ApiError(
            "not_found",
            f"Roboflow {workspace}/{project_id}/{version} has no trained model to download",
        )
    trained_model = trained[0]
    model_type = getattr(trained_model, "model_type", None)

    # the SDK writes into the working directory, so give it one of its own
    previous = os.getcwd()
    os.chdir(out_dir)
    try:
        trained_model.download()
    except Exception as exc:
        raise ApiError(
            "upstream_error",
            f"Roboflow would not release the weights for this version ({str(exc)[:160]}). "
            "Hosted-only architectures cannot be downloaded.",
        ) from exc
    finally:
        os.chdir(previous)

    produced = [
        os.path.join(out_dir, name) for name in os.listdir(out_dir)
        if name.endswith((".pt", ".pth", ".onnx"))
    ]
    if not produced:
        raise ApiError(
            "not_found",
            "Roboflow produced no weights file for this version - it may be hosted-only",
        )
    return max(produced, key=os.path.getsize), model_type
