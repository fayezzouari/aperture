"""The runtime's HTTP surface.

Only the gateway and the control plane talk to this, over the internal token.
Three endpoints matter:

  POST /v1/infer     - batched inference on a resident version
  POST /v1/validate  - load a candidate ad hoc, run it once, render the overlay (R1.7)
  GET  /healthz      - liveness; /readyz reports which versions are actually servable
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import io
import logging
import time
from contextlib import asynccontextmanager

import numpy as np
from aperture_core.configspec import ModelConfig
from aperture_core.images import ImageDecodeError, decode_image_bytes, image_stats
from aperture_core.overlay import render_png
from aperture_core.postprocess import confidence_summary, postprocess
from aperture_core.preprocess import preprocess_batch
from aperture_core.results import ImageResult
from aperture_core.sandbox import HookError
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from PIL import Image
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from pydantic import BaseModel, Field

from runtime.backends.base import MissingInputError
from runtime.batcher import Deadline, QueueFull
from runtime.control import ControlPlane
from runtime.device import detect
from runtime.memory import request_need_mb
from runtime.settings import settings
from runtime.store import ModelStore

log = logging.getLogger("runtime.main")

REGISTRY = CollectorRegistry()
infer_requests = Counter("aperture_runtime_requests_total", "Requests handled by this runtime",
                         ["version", "status"], registry=REGISTRY)
infer_latency = Histogram("aperture_runtime_latency_seconds", "Runtime-side latency",
                          ["version", "stage"],
                          buckets=(0.002, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
                          registry=REGISTRY)
loaded_gauge = Gauge("aperture_runtime_loaded_versions", "Versions resident on this runtime",
                     registry=REGISTRY)
queue_gauge = Gauge("aperture_runtime_queue", "Queue depth per version", ["version"],
                    registry=REGISTRY)

state: dict = {}


class ImagePayload(BaseModel):
    b64: str
    name: str = "image"


class InferRequest(BaseModel):
    request_id: str
    version_id: str
    images: list[ImagePayload]
    # R3.3 - a multi-input model's non-image inputs, one row per image
    inputs: dict[str, list] = Field(default_factory=dict)
    overrides: dict = Field(default_factory=dict)
    want_overlay: bool = False
    want_stats: bool = True
    extra: dict = Field(default_factory=dict)
    deadline_ms: int = 30_000


class ValidateRequest(BaseModel):
    version_id: str
    inputs: dict[str, list] = Field(default_factory=dict)
    weights_bucket: str
    weights_key: str
    weights_sha256: str | None = None
    artifact_format: str = "onnx"
    config: dict
    image_b64: str
    want_overlay: bool = True


def _configure_logging() -> None:
    import json
    import sys

    class Formatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            payload = {"ts": self.formatTime(record), "level": record.levelname,
                       "logger": record.name, "msg": record.getMessage(),
                       "runtime_id": state.get("runtime_id")}
            for key, value in record.__dict__.items():
                if key not in logging.LogRecord("", 0, "", 0, "", (), None).__dict__ and \
                        key not in ("message", "asctime", "taskName"):
                    payload[key] = value
            if record.exc_info:
                payload["exception"] = self.formatException(record.exc_info)
            return json.dumps(payload, default=str)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(Formatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level.upper())
    logging.getLogger("uvicorn.access").setLevel("WARNING")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _configure_logging()
    device_info = detect()
    store = ModelStore(device_info)
    control = ControlPlane(store, device_info)
    state.update({"store": store, "control": control, "device": device_info,
                  "runtime_id": control.runtime_id, "started_at": time.time()})
    log.info("runtime starting", extra={"runtime_id": control.runtime_id,
                                        "device": device_info.get("device"),
                                        "providers": device_info.get("providers")})
    await control.start()
    yield
    await control.stop()
    await store.shutdown()
    log.info("runtime stopped")


app = FastAPI(title="Aperture Runtime", version="0.1.0", lifespan=_lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)


def _auth(token: str | None) -> None:
    if not token or not hmac.compare_digest(token, settings.runtime_internal_token):
        raise _error(401, "unauthenticated", "invalid runtime token")


class _RuntimeError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        self.status, self.code, self.message, self.details = status, code, message, details or {}


def _error(status: int, code: str, message: str, details: dict | None = None) -> _RuntimeError:
    return _RuntimeError(status, code, message, details)


@app.exception_handler(_RuntimeError)
async def _handle(request: Request, exc: _RuntimeError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
    )


@app.post("/v1/infer")
async def infer(body: InferRequest, x_runtime_token: str = Header(None)) -> dict:
    _auth(x_runtime_token)
    store: ModelStore = state["store"]
    entry = store.get(body.version_id)
    if entry is None:
        raise _error(503, "no_replica_available",
                     "this runtime does not hold that version; the router should not have "
                     "sent it here")
    if entry.state != "ready":
        raise _error(503, "runtime_unhealthy",
                     f"this version is '{entry.state}' on this replica: {entry.detail}"[:300])

    config: ModelConfig = entry.config.apply_overrides(body.overrides)
    if body.extra:
        config = config.model_copy(update={"extra": {**(config.extra or {}), **body.extra}})

    started = time.perf_counter()
    try:
        blobs, sizes = await asyncio.to_thread(_read_headers, body.images)
    except ImageDecodeError as exc:
        infer_requests.labels(str(entry.version), "invalid_image").inc()
        raise _error(400, "invalid_image", str(exc))

    # reserve working memory before decoding anything, waiting for in-flight
    # requests to finish when they hold it (see memory.Admission)
    admission = store.requests
    buffers_mb = request_need_mb(sizes)
    wait_s = min(settings.request_admission_wait_ms, body.deadline_ms) / 1000
    waited_from = time.perf_counter()
    ticket, need_mb, headroom = await admission.reserve(
        body.version_id, len(sizes), buffers_mb, entry.activation_mb,
        timeout_s=wait_s, max_waiting=settings.request_admission_max_waiting)
    # the wait is spent from the caller's deadline, not added to it
    body.deadline_ms = max(1, body.deadline_ms - int((time.perf_counter() - waited_from) * 1000))
    if ticket is None:
        infer_requests.labels(str(entry.version), "queue_full").inc()
        details = {"needs_mb": need_mb, "headroom_mb": headroom,
                   "inflight_reserved_mb": admission.reserved_mb,
                   "inflight_requests": admission.in_flight,
                   "waiting_requests": admission.waiting}
        # too large only when the images themselves can never fit: activation for a
        # second concurrent request is room that frees up, not a property of the payload
        if buffers_mb > max(headroom, admission.request_space_mb()):
            raise _error(413, "payload_too_large",
                         f"these images need ~{buffers_mb} MB of working memory and this replica "
                         f"has {headroom} MB beside its resident models; send smaller images "
                         "or fewer per request", details)
        if admission.in_flight == 0:
            # nothing to wait for here: the resident models leave too little room
            raise _error(503, "queue_full",
                         f"this replica's resident models leave {headroom} MB for requests and "
                         f"these images need ~{need_mb} MB; another replica, a smaller image or "
                         "more runtime memory will serve it", details)
        raise _error(503, "queue_full",
                     f"this replica has had no memory for another request for {wait_s:.0f} s "
                     f"or {admission.waiting} are already waiting (needs ~{need_mb} MB, "
                     f"{headroom - admission.reserved_mb} MB free); retry on another replica",
                     details)
    try:
        return await _infer_admitted(body, entry, config, blobs, started)
    finally:
        admission.release(ticket)


async def _infer_admitted(body: InferRequest, entry, config: ModelConfig, blobs: list[bytes],
                          started: float) -> dict:
    try:
        arrays, stats = await asyncio.to_thread(_decode_all, blobs, body.images,
                                                config.preprocess.auto_orient, body.want_stats)
    except ImageDecodeError as exc:
        infer_requests.labels(str(entry.version), "invalid_image").inc()
        raise _error(400, "invalid_image", str(exc))

    try:
        tensor, geoms = await asyncio.to_thread(preprocess_batch, arrays, config)
    except ValueError as exc:
        raise _error(400, "invalid_request", str(exc))
    except HookError as exc:
        raise _error(400, "invalid_config", f"the preprocessing hook failed: {exc}")
    preprocess_ms = (time.perf_counter() - started) * 1000

    try:
        extras = _extra_inputs(body.inputs, len(arrays))
    except ValueError as exc:
        raise _error(400, "invalid_request", str(exc))

    assert entry.batcher is not None
    try:
        result = await entry.batcher.submit(tensor, deadline_ms=body.deadline_ms, extras=extras)
    except QueueFull as exc:
        infer_requests.labels(str(entry.version), "queue_full").inc()
        raise _error(503, "queue_full", str(exc),
                     {"queue_depth": exc.depth, "max_queue_depth": exc.limit})
    except Deadline as exc:
        infer_requests.labels(str(entry.version), "timeout").inc()
        raise _error(504, "timeout", str(exc))
    except MissingInputError as exc:
        infer_requests.labels(str(entry.version), "invalid_request").inc()
        raise _error(400, "invalid_request", str(exc))
    except Exception as exc:
        infer_requests.labels(str(entry.version), "inference_failed").inc()
        log.exception("inference failed", extra={"version_id": body.version_id})
        raise _error(500, "inference_failed", f"the forward pass failed: {exc}"[:400])

    post_started = time.perf_counter()
    try:
        results = await asyncio.to_thread(postprocess, result.outputs, config, geoms)
    except HookError as exc:
        raise _error(400, "invalid_config", f"the postprocessing hook failed: {exc}")
    except Exception as exc:
        raise _error(
            500, "inference_failed",
            f"the model ran but its output could not be decoded with this config: {exc}"[:400],
        )
    postprocess_ms = (time.perf_counter() - post_started) * 1000

    overlays: list[str] = []
    if body.want_overlay:
        overlays = await asyncio.to_thread(
            lambda: [base64.b64encode(render_png(arrays[i], results[i])).decode()
                     for i in range(len(results))]
        )

    entry.requests += 1
    entry.batches = entry.batcher.batches
    runtime_ms = (time.perf_counter() - started) * 1000
    version_label = str(entry.version)
    infer_requests.labels(version_label, "ok").inc()
    infer_latency.labels(version_label, "preprocess").observe(preprocess_ms / 1000)
    infer_latency.labels(version_label, "inference").observe(result.inference_ms / 1000)
    infer_latency.labels(version_label, "postprocess").observe(postprocess_ms / 1000)
    infer_latency.labels(version_label, "total").observe(runtime_ms / 1000)
    queue_gauge.labels(version_label).set(entry.batcher.queue_depth)

    return {
        "request_id": body.request_id,
        "version_id": body.version_id,
        "runtime_id": state["runtime_id"],
        "results": [r.model_dump(exclude_none=True) for r in results],
        "stats": stats,
        "overlays": overlays,
        "batch_executed": result.batch_executed,
        "latency": {
            "queue_ms": round(max(result.queue_ms, 0.0), 3),
            "preprocess_ms": round(preprocess_ms, 3),
            "inference_ms": round(result.inference_ms, 3),
            "postprocess_ms": round(postprocess_ms, 3),
            "runtime_ms": round(runtime_ms, 3),
        },
        "confidence": confidence_summary(results),
    }


def _extra_inputs(raw: dict[str, list], batch: int) -> dict[str, np.ndarray]:
    """Named model inputs from the request, normalised to (batch, ...) float32.

    One row is broadcast across the batch, which is what a caller sending the
    same geometry for every image in a batch expects.
    """
    out: dict[str, np.ndarray] = {}
    for name, value in (raw or {}).items():
        array = np.asarray(value, dtype=np.float32)
        if array.ndim == 1:
            array = array[None, ...]
        if array.shape[0] not in (1, batch):
            raise ValueError(
                f"input '{name}' has {array.shape[0]} rows but the request carries {batch} "
                "image(s) - send one row per image, or a single row to use for all of them"
            )
        if array.shape[0] == 1 and batch > 1:
            array = np.repeat(array, batch, axis=0)
        out[name] = array
    return out


def _read_headers(images: list[ImagePayload]) -> tuple[list[bytes], list[tuple[int, int]]]:
    """Raw bytes and pixel dimensions of each image, without decoding any pixels."""
    blobs, sizes = [], []
    for index, payload in enumerate(images):
        raw = payload.b64
        if raw.startswith("data:"):
            raw = raw.split(",", 1)[1]
        try:
            data = base64.b64decode(raw, validate=False)
        except Exception as exc:
            raise ImageDecodeError(f"image {index} is not valid base64") from exc
        try:
            with Image.open(io.BytesIO(data)) as header:  # lazy: reads the header only
                sizes.append(header.size)
        except Exception as exc:
            raise ImageDecodeError(f"image {index} is not a readable image: {exc}") from exc
        blobs.append(data)
    return blobs, sizes


def _decode_all(blobs: list[bytes], images: list[ImagePayload], auto_orient: bool,
                want_stats: bool) -> tuple[list[np.ndarray], list[dict]]:
    arrays, stats = [], []
    for data, payload in zip(blobs, images):
        loaded = decode_image_bytes(data, name=payload.name, auto_orient=auto_orient)
        arrays.append(loaded.array)
        if want_stats:
            stats.append({"name": payload.name, **image_stats(loaded.array)})
    return arrays, stats


@app.post("/v1/validate")
async def validate(body: ValidateRequest, x_runtime_token: str = Header(None)) -> dict:
    """R1.7 - load a candidate outside the serving set, run it once, then let it go."""
    _auth(x_runtime_token)
    store: ModelStore = state["store"]

    try:
        config = ModelConfig.model_validate(body.config)
    except Exception as exc:
        return {"ok": False, "errors": [f"the config itself is not valid: {exc}"],
                "warnings": [], "timings": {}, "summary": {}, "io": {}}

    assignment = {
        "version_id": body.version_id, "weights_bucket": body.weights_bucket,
        "weights_key": body.weights_key, "weights_sha256": body.weights_sha256,
        "artifact_format": body.artifact_format, "config": body.config, "device": "auto",
    }

    backend = None
    try:
        path = await store._fetch_weights(assignment)  # noqa: SLF001 - same package
        backend = await asyncio.to_thread(store._build_backend, config, path, assignment)  # noqa: SLF001
    except Exception as exc:
        return {"ok": False, "errors": [str(exc)[:1000]], "warnings": [], "timings": {},
                "summary": {}, "io": {}}

    try:
        raw = body.image_b64.split(",", 1)[1] if body.image_b64.startswith("data:") \
            else body.image_b64
        loaded = decode_image_bytes(base64.b64decode(raw), auto_orient=config.preprocess.auto_orient)

        t0 = time.perf_counter()
        tensor, geoms = await asyncio.to_thread(preprocess_batch, [loaded.array], config)
        t1 = time.perf_counter()
        outputs = await asyncio.to_thread(backend.infer, tensor,
                                          _extra_inputs(body.inputs, 1) or None)
        t2 = time.perf_counter()
        results = await asyncio.to_thread(postprocess, outputs, config, geoms)
        t3 = time.perf_counter()

        overlay_b64 = None
        if body.want_overlay:
            overlay_b64 = base64.b64encode(
                await asyncio.to_thread(render_png, loaded.array, results[0])
            ).decode()

        io_info = {
            "input_names": backend.io.input_names,
            "output_names": backend.io.output_names,
            "input_shapes": backend.io.input_shapes,
            "output_shapes": {k: [str(d) for d in v]
                              for k, v in backend.io.output_shapes.items()},
            "provider": backend.io.provider,
            "dynamic_batch": backend.io.dynamic_batch,
            "image_width": loaded.width,
            "image_height": loaded.height,
            "tensor_shape": list(tensor.shape),
            "class_columns": _infer_class_columns(outputs, config),
        }
        return {
            "ok": True,
            "errors": [],
            "warnings": _shape_warnings(tensor, backend, config),
            "timings": {
                "preprocess_ms": round((t1 - t0) * 1000, 3),
                "inference_ms": round((t2 - t1) * 1000, 3),
                "postprocess_ms": round((t3 - t2) * 1000, 3),
                "total_ms": round((t3 - t0) * 1000, 3),
            },
            "summary": {**confidence_summary(results),
                        **_extent(results[0])},
            "io": io_info,
            "result": results[0].model_dump(exclude_none=True),
            "overlay_b64": overlay_b64,
        }
    except Exception as exc:
        log.warning("validation run failed", exc_info=True)
        return {"ok": False, "errors": [str(exc)[:1000]], "warnings": [], "timings": {},
                "summary": {}, "io": {}}
    finally:
        if backend is not None:
            await asyncio.to_thread(backend.close)


def _infer_class_columns(outputs: list[np.ndarray], config: ModelConfig) -> int | None:
    """How many class columns the head actually emits - catches an off-by-one class list."""
    from aperture_core.configspec import Decoder

    decoder = config.postprocess.decoder
    if not outputs:
        return None
    first = outputs[0]
    if decoder in (Decoder.classifier_softmax, Decoder.classifier_sigmoid,
                   Decoder.classifier_probs):
        return int(first.reshape(first.shape[0], -1).shape[1])
    if decoder == Decoder.detr:
        return int(first.shape[-1]) if first.ndim == 3 else None
    if decoder in (Decoder.yolo_v5, Decoder.yolo_v8):
        if first.ndim != 3:
            return None
        channels = min(first.shape[1], first.shape[2])
        return int(channels - (5 if decoder == Decoder.yolo_v5 else 4))
    return None


def _extent(result: ImageResult) -> dict:
    boxes = [d.box for d in (result.detections or [])]
    if not boxes:
        return {}
    return {"box_extent": {
        "min_x": round(min(b[0] for b in boxes), 2),
        "min_y": round(min(b[1] for b in boxes), 2),
        "max_x": round(max(b[2] for b in boxes), 2),
        "max_y": round(max(b[3] for b in boxes), 2),
    }}


def _shape_warnings(tensor: np.ndarray, backend, config: ModelConfig) -> list[str]:
    warnings: list[str] = []
    if not backend.io.input_names:
        return warnings
    declared = backend.io.input_shapes.get(backend.io.input_names[0]) or []
    actual = list(tensor.shape)
    if len(declared) == len(actual):
        for index, (want, got) in enumerate(zip(declared, actual)):
            if isinstance(want, int) and want > 0 and want != got:
                warnings.append(
                    f"the model expects {declared} at its input but preprocessing produced "
                    f"{actual} (axis {index}); adjust preprocess.resize or input.layout"
                )
                break
    if not backend.io.dynamic_batch and config.warmup.batch_sizes not in ([1], []):
        warnings.append(
            "this graph has a fixed batch dimension, so dynamic batching will be disabled "
            "for it - re-export with a dynamic batch axis to get batching"
        )
    return warnings


@app.get("/healthz")
async def healthz() -> PlainTextResponse:
    return PlainTextResponse("ok")


@app.get("/readyz")
async def readyz() -> JSONResponse:
    store: ModelStore = state.get("store")
    control: ControlPlane = state.get("control")
    if store is None:
        return JSONResponse({"ready": False, "reason": "still starting"}, status_code=503)
    ready = store.ready_versions()
    failed = [m.version_id for m in store.models.values() if m.state == "failed"]
    return JSONResponse({
        "ready": True,
        "runtime_id": state.get("runtime_id"),
        "device": state.get("device", {}).get("device"),
        "control_plane_connected": bool(control and control.connected),
        "control_plane_error": control.last_error if control else None,
        "versions_ready": ready,
        "versions_failed": failed,
        "memory": store.memory(),
        "uptime_s": round(time.time() - state.get("started_at", time.time()), 1),
    })


@app.get("/v1/state")
async def runtime_state(x_runtime_token: str = Header(None)) -> dict:
    _auth(x_runtime_token)
    store: ModelStore = state["store"]
    return {
        "runtime_id": state["runtime_id"],
        "device": state["device"],
        "memory": store.memory(),
        "loaded": [
            {**m.report(), "model": m.model_slug, "version": m.version,
             "provider": m.backend.io.provider if m.backend else None,
             "max_batch_size": m.batcher.max_batch_size if m.batcher else None,
             "max_wait_ms": m.batcher.max_wait_ms if m.batcher else None,
             "batching_enabled": m.batcher.splittable if m.batcher else None}
            for m in store.models.values()
        ],
    }


@app.get("/metrics")
async def metrics() -> PlainTextResponse:
    store: ModelStore = state.get("store")
    if store is not None:
        loaded_gauge.set(len(store.models))
        for entry in store.models.values():
            if entry.batcher is not None:
                queue_gauge.labels(str(entry.version)).set(entry.batcher.queue_depth)
    return PlainTextResponse(generate_latest(REGISTRY).decode(), media_type=CONTENT_TYPE_LATEST)
