"""R3.1-R3.7 - the inference API products call.

    POST /v1/models/{model}/{version|tag}/predict

One route accepts multipart uploads, base64 JSON and remote URLs, because that
is what different callers already have in hand (a browser has a file, a backend
has bytes, a pipeline has an S3 URL).
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from aperture_core.formats import FORMATS, to_roboflow_batch
from aperture_core.results import LatencyBreakdown, PredictResponse
from app import telemetry
from app.deps import Principal, get_model_or_404, inference_principal, resolve_project
from app.db import get_session
from pydantic import ValidationError

from app.errors import ApiError
from app.metrics import (
    batch_size as batch_hist,
    errors_total,
    images_total,
    observe_stages,
    request_latency,
    requests_total,
)
from app.schemas import PredictJSON
from app.security import key_allows_model
from app.settings import settings
from app.services import capture as capture_service
from app.services.gateway import (
    InputImage,
    Stopwatch,
    build_runtime_payload,
    call_runtime,
    decode_b64_input,
    fetch_url_image,
    mirror_to_shadow,
    new_request_id,
    pick_replica,
    resolve_target,
    sanitise_overrides,
    _guard_size,
)
from app.services.ondemand import ensure_serving
from app.services.resolver import resolve

log = logging.getLogger("aperture.predict")
router = APIRouter(tags=["inference"])

# FastAPI runs BackgroundTasks off the *response*, so a handler that raises
# drops them - which would mean the failed requests, the ones worth reading
# later, never get logged. These fire on the loop instead, with a strong
# reference so they cannot be garbage collected mid-flight.
_pending: set[asyncio.Task] = set()


def spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _pending.add(task)
    task.add_done_callback(_pending.discard)

MULTIPART_FIELDS = ("images", "image", "file", "files")


async def _collect(request: Request) -> tuple[list[InputImage], PredictJSON]:
    content_type = (request.headers.get("content-type") or "").lower()
    images: list[InputImage] = []

    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        body = PredictJSON(
            overrides=_json_field(form, "overrides"),
            inputs=_json_field(form, "inputs"),
            extra=_json_field(form, "extra"),
            include_overlay=str(form.get("include_overlay", "")).lower() in ("1", "true", "yes"),
            capture=_bool_field(form.get("capture")),
            format=(form.get("format") if isinstance(form.get("format"), str) else None),
        )
        text_fields: list[str] = []
        for key in form.keys():
            for value in form.getlist(key):
                if not hasattr(value, "read"):
                    text_fields.append(key)
        for field in MULTIPART_FIELDS:
            for upload in form.getlist(field):
                if hasattr(upload, "read"):
                    images.append(
                        InputImage(data=await upload.read(),
                                   name=getattr(upload, "filename", None) or field,
                                   source="upload")
                    )
        if not images:
            # The usual cause is a form-data row left as Text with a path typed
            # into it: the field arrives as a string, so there is no file to
            # read. "no image supplied" alone sends people looking at the URL.
            offenders = [name for name in text_fields if name in MULTIPART_FIELDS]
            raise ApiError(
                "invalid_request",
                ("multipart form-data with no file in it. "
                 + (f"Field(s) {', '.join(sorted(set(offenders)))} arrived as text, not a file - "
                    "in Postman switch that row's type from Text to File. "
                    if offenders else
                    f"Fields present: {', '.join(sorted(set(text_fields))) or '(none)'}. ")
                 + f"Attach the image as a file field named one of: "
                   f"{', '.join(MULTIPART_FIELDS)}."),
                details={"fields_received": sorted(set(text_fields)),
                         "file_fields_accepted": list(MULTIPART_FIELDS),
                         "text_rows_that_should_be_files": sorted(set(offenders))},
            )
        for url in form.getlist("image_url") + form.getlist("image_urls"):
            if isinstance(url, str) and url.strip():
                images.append(await fetch_url_image(url.strip()))
        return images, body

    if content_type.startswith("application/json") or not content_type:
        try:
            raw = await request.json() if await request.body() else {}
        except ValueError as exc:
            raise ApiError("invalid_request", f"the request body is not valid JSON: {exc}")
        # A body in the wrong shape is the caller's mistake, so it has to come
        # back as one. Letting pydantic's error escape turns a typo into a 500
        # "an unexpected error occurred", which says nothing about the field that
        # was wrong and logs it as a fault on this side.
        try:
            body = PredictJSON.model_validate(raw or {})
        except ValidationError as exc:
            problems = [
                f"{'.'.join(str(part) for part in error['loc']) or 'body'}: {error['msg']}"
                for error in exc.errors()[:4]
            ]
            raise ApiError(
                "invalid_request",
                "this request body does not match the schema - " + "; ".join(problems),
                details={"errors": problems,
                         "hint": "images takes a list of base64 strings or data URLs; "
                                 "use image_urls for remote images, or send multipart "
                                 "form-data with repeated 'images' file fields"},
            )
        payloads = list(body.images or [])
        if body.image:
            payloads.append(body.image)
        for index, payload in enumerate(payloads):
            images.append(decode_b64_input(payload, index))
        urls = list(body.image_urls or [])
        if body.image_url:
            urls.append(body.image_url)
        for url in urls:
            images.append(await fetch_url_image(url))
        return images, body

    if content_type.startswith("image/") or content_type == "application/octet-stream":
        data = await request.body()
        return [InputImage(data=data, name="body", source="upload")], PredictJSON()

    raise ApiError(
        "invalid_request",
        f"unsupported content-type '{content_type}'; use multipart/form-data, application/json "
        "or a raw image body",
    )


def _json_field(form, key: str) -> dict | None:
    import json

    raw = form.get(key)
    if not raw or not isinstance(raw, str):
        return None
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise ApiError("invalid_request", f"form field '{key}' is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ApiError("invalid_request", f"form field '{key}' must be a JSON object")
    return value


def _bool_field(raw) -> bool | None:
    if raw is None or raw == "":
        return None
    return str(raw).lower() in ("1", "true", "yes")


@router.post(
    "/models/{model}/{ref}/predict",
    response_model=PredictResponse,
    response_model_exclude_none=True,
    summary="Run inference on one or more images",
)
async def predict(
    model: str,
    ref: str,
    request: Request,
    response: Response,
    format: str | None = Query(
        None,
        description="'aperture' (default) or 'roboflow' for a Roboflow-shaped response",
    ),
    ondemand: bool = Query(
        False,
        description="Start a replica for this version if none is running, and wait for it. "
                    "Off by default: a client calling an undeployed model should get a 503, "
                    "not an unplanned model load. The playground turns it on.",
    ),
    principal: Principal = Depends(inference_principal),
    session: AsyncSession = Depends(get_session),
) -> PredictResponse:
    watch = Stopwatch()
    request_id = getattr(request.state, "request_id", None) or new_request_id()
    trace_id, span_id = telemetry.current_ids()
    traceparent = {"traceparent": request.headers["traceparent"]} if "traceparent" in request.headers else {}

    project = await resolve_project(request, principal, session)
    model_row = await get_model_or_404(session, project.id, model)

    if principal.kind == "api_key" and principal.api_key is not None:
        if not key_allows_model(principal.api_key, model_row.id, model_row.slug):
            raise ApiError(
                "key_not_scoped_to_model",
                f"this API key is not scoped to '{model_row.slug}'",
                details={"model": model_row.slug},
            )

    # Everything from intake to routing can fail, and those failures are the
    # ones an operator most wants to find later ("why did the client get a 503
    # at 02:14?"). So they are logged like any other request, not dropped.
    images: list[InputImage] = []
    try:
        images, body = await _collect(request)
        _guard_size(sum(len(i.data) for i in images), len(images))
        overrides = sanitise_overrides(body.overrides)
        if ondemand:
            principal.require(project.id, "developer")
            picked = await resolve(session, model_row, ref)
            await ensure_serving(session, project, model_row, picked.version, principal,
                                 client_ip=_client_ip(request))
        resolved, target, shadows = await resolve_target(session, model_row, ref)
    except ApiError as exc:
        spawn(_log_rejection(
            project=project, model_row=model_row, ref=ref, request_id=request_id,
            trace_id=trace_id, span_id=span_id, principal=principal,
            client_ip=_client_ip(request), error=exc, elapsed_ms=watch.ms(),
            images=[(i.data, i.name) for i in images],
        ))
        requests_total.labels(project.slug, model_row.slug, "unknown", "primary",
                              "rejected" if exc.http_status in (429, 503) else "error").inc()
        errors_total.labels(model_row.slug, "unknown", exc.code).inc()
        response.headers["x-request-id"] = request_id
        raise

    timeout_ms = target.deployment.request_timeout_ms
    version_label = str(target.version.version)

    input_ref = {
        "count": len(images),
        "sources": sorted({i.source for i in images}),
        "names": [i.name for i in images][:16],
        "bytes": sum(len(i.data) for i in images),
        "sha256": [i.sha256 for i in images][:16],
    }

    want_overlay = body.include_overlay
    payload = build_runtime_payload(
        target, images, request_id=request_id, overrides=overrides,
        want_overlay=want_overlay, extra=body.extra, deadline_ms=timeout_ms,
        inputs=body.inputs,
    )

    error: ApiError | None = None
    reply = None
    replica = None
    try:
        with telemetry.start_span("aperture.inference", {
            "aperture.model": model_row.slug,
            "aperture.version": target.version.version,
            "aperture.route": target.route,
            "aperture.batch_size": len(images),
        }):
            replica = await pick_replica(target.version.id, model_row.slug)
            reply = await call_runtime(replica, payload, timeout_ms=timeout_ms,
                                       trace_headers=traceparent)
    except ApiError as exc:
        error = exc

    gateway_ms = watch.ms()
    runtime_latency = (reply.latency if reply else {}) or {}
    latency = {
        "queue_ms": runtime_latency.get("queue_ms", 0.0),
        "preprocess_ms": runtime_latency.get("preprocess_ms", 0.0),
        "inference_ms": runtime_latency.get("inference_ms", 0.0),
        "postprocess_ms": runtime_latency.get("postprocess_ms", 0.0),
        "runtime_ms": runtime_latency.get("runtime_ms", 0.0),
        "gateway_ms": round(gateway_ms - runtime_latency.get("runtime_ms", 0.0), 3),
        "total_ms": round(gateway_ms, 3),
    }

    status = "ok" if error is None else (
        "rejected" if error.http_status in (429, 503) else
        "timeout" if error.code == "timeout" else "error"
    )
    labels = (project.slug, model_row.slug, version_label, target.route, status)
    requests_total.labels(*labels).inc()
    request_latency.labels(model_row.slug, version_label, target.route).observe(gateway_ms / 1000)
    if error is not None:
        errors_total.labels(model_row.slug, version_label, error.code).inc()
    else:
        images_total.labels(project.slug, model_row.slug, version_label).inc(len(images))
        observe_stages(model_row.slug, version_label, latency)
        if reply and reply.batch_executed:
            batch_hist.labels(model_row.slug, version_label).observe(reply.batch_executed)

    results = reply.results if reply else []
    stats = (reply.stats if reply else []) or []
    from aperture_core.postprocess import confidence_summary
    from aperture_core.results import ImageResult

    parsed = [ImageResult.model_validate(r) for r in results] if results else []
    confidence = confidence_summary(parsed) if parsed else {}
    detection_count = sum(len(r.detections or []) + len(r.classifications or []) +
                          len(r.texts or []) for r in parsed)

    do_capture = capture_service.should_capture(
        project_rate=project.capture_sample_rate, forced=body.capture, is_error=error is not None
    )
    capture_meta: dict = {}
    if do_capture:
        capture_meta = {"pending": True}
        spawn(_capture_and_log(
            project=project, model_row=model_row, target=target, resolved_tag=resolved.tag,
            request_id=request_id, trace_id=trace_id, span_id=span_id, principal=principal,
            client_ip=_client_ip(request), status=status, error=error, latency=latency,
            reply_runtime_id=(reply.runtime_id if reply else (replica or {}).get("runtime_id")),
            input_ref=input_ref, stats=stats, results=results, confidence=confidence,
            overrides=overrides, detection_count=detection_count,
            images=[(i.data, i.name) for i in images],
            overlays=(reply.overlays if reply and want_overlay else []),
        ))
    else:
        spawn(_log_only(
            project=project, model_row=model_row, target=target, resolved_tag=resolved.tag,
            request_id=request_id, trace_id=trace_id, span_id=span_id, principal=principal,
            client_ip=_client_ip(request), status=status, error=error, latency=latency,
            reply_runtime_id=(reply.runtime_id if reply else (replica or {}).get("runtime_id")),
            input_ref=input_ref, stats=stats, results=results, confidence=confidence,
            overrides=overrides, detection_count=detection_count,
        ))

    if shadows:
        spawn(mirror_to_shadow(shadows, images, request_id=request_id, overrides=overrides,
                               trace_headers=traceparent))

    response.headers["x-request-id"] = request_id
    response.headers["x-aperture-model-version"] = version_label
    response.headers["x-aperture-route"] = target.route
    if trace_id:
        response.headers["x-trace-id"] = trace_id
    remaining = getattr(request.state, "rate_remaining", None)
    if remaining is not None and remaining >= 0:
        response.headers["x-ratelimit-remaining"] = str(remaining)

    if error is not None:
        raise error

    wanted_format = (format or body.format or settings.default_response_format).lower()
    if wanted_format not in FORMATS:
        raise ApiError("invalid_request",
                       f"unknown response format '{wanted_format}'; use one of "
                       + ", ".join(FORMATS))

    if wanted_format == "roboflow":
        # Roboflow's shape, so existing clients need no change. The resolved
        # version still travels in the headers, where it does not collide with
        # their schema.
        payload = to_roboflow_batch(
            parsed, task=model_row.task,
            decoder=(target.version.config or {}).get("postprocess", {}).get("decoder", ""),
            inference_id=request_id, time_s=latency["total_ms"] / 1000,
            model=model_row.slug, version=target.version.version,
        )
        return JSONResponse(content=payload, headers=dict(response.headers))

    overlay_payload = reply.overlays if (reply and want_overlay) else []
    out = PredictResponse(
        request_id=request_id,
        model=model_row.slug,
        model_version=target.version.version,
        version_id=target.version.id,
        tag=resolved.tag,
        # the version's own task, not the model's: a conversion can legitimately
        # produce a segmentation version of a model created as detection
        task=(target.version.config or {}).get("task") or model_row.task,
        batch_size=len(images),
        results=parsed,
        latency=LatencyBreakdown(**latency),
        runtime_id=reply.runtime_id if reply else None,
        trace_id=trace_id,
        overrides_applied=overrides,
        overlays=overlay_payload or None,
    )
    return out


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


async def _log_only(**kw) -> None:
    await _emit_record(capture={}, **kw)


async def _capture_and_log(*, images: list[tuple[bytes, str]], overlays: list[str], **kw) -> None:
    stored = await capture_service.store_payloads(
        project_slug=kw["project"].slug, model_slug=kw["model_row"].slug,
        request_id=kw["request_id"], images=images, overlays=overlays,
    )
    await _emit_record(capture=stored, **kw)


async def _emit_record(*, project, model_row, target, resolved_tag, request_id, trace_id, span_id,
                       principal, client_ip, status, error, latency, reply_runtime_id, input_ref,
                       stats, results, confidence, overrides, detection_count, capture) -> None:
    record = capture_service.build_record(
        request_id=request_id, trace_id=trace_id, span_id=span_id, project_id=project.id,
        project_slug=project.slug, model=model_row, version=target.version, tag=resolved_tag,
        route=target.route, principal_kind=principal.kind, principal_id=principal.id,
        principal_label=principal.label, client_ip=client_ip, status=status,
        http_status=error.http_status if error else 200,
        error_code=error.code if error else None,
        error_message=error.message if error else None,
        batch_size=input_ref.get("count", 0), latency=latency, runtime_id=reply_runtime_id,
        input_ref=input_ref,
        input_stats={"images": stats[:8], "summary": _stats_summary(stats)},
        output=capture_service.truncate_output(results), confidence=confidence,
        overrides=overrides, capture=capture, detection_count=detection_count,
        retention=capture_service.retention_days(project.capture_retention_days),
    )
    await capture_service.emit(record)


def _stats_summary(stats: list[dict]) -> dict:
    if not stats:
        return {}
    keys = ("width", "height", "brightness", "contrast", "megapixels")
    out: dict[str, float] = {}
    for key in keys:
        values = [s[key] for s in stats if isinstance(s.get(key), (int, float))]
        if values:
            out[key] = round(sum(values) / len(values), 4)
    return out


async def _log_rejection(*, project, model_row, ref, request_id, trace_id, span_id, principal,
                         client_ip, error, elapsed_ms, images) -> None:
    """R5.1 for requests that never reached a runtime.

    A rejected request still gets a row, with the same shape as a successful
    one, so `status=rejected` in the explorer is a complete answer rather than a
    partial one. If the caller sent an image and capture is on, keep it too -
    a rejection you cannot reproduce is hard to argue about.
    """
    capture_meta: dict = {}
    is_error = True
    if images and capture_service.should_capture(
        project_rate=project.capture_sample_rate, forced=None, is_error=is_error
    ):
        capture_meta = await capture_service.store_payloads(
            project_slug=project.slug, model_slug=model_row.slug, request_id=request_id,
            images=images, overlays=[],
        )

    record = capture_service.build_record(
        request_id=request_id, trace_id=trace_id, span_id=span_id, project_id=project.id,
        project_slug=project.slug, model=model_row, version=None, tag=_tag_hint(ref),
        route="primary", principal_kind=principal.kind, principal_id=principal.id,
        principal_label=principal.label, client_ip=client_ip,
        status="rejected" if error.http_status in (429, 503) else "error",
        http_status=error.http_status, error_code=error.code, error_message=error.message,
        batch_size=len(images),
        latency={"total_ms": round(elapsed_ms, 3), "gateway_ms": round(elapsed_ms, 3)},
        runtime_id=None,
        input_ref={"count": len(images), "sources": ["upload"] if images else [],
                   "bytes": sum(len(data) for data, _ in images)},
        input_stats={}, output={}, confidence={}, overrides={}, capture=capture_meta,
        detection_count=0,
        retention=capture_service.retention_days(project.capture_retention_days),
    )
    await capture_service.emit(record)


def _tag_hint(ref: str) -> str | None:
    """A numeric ref is a version, anything else was a tag the caller asked for."""
    raw = (ref or "").strip()
    stripped = raw[1:] if raw[:1].lower() == "v" else raw
    return None if stripped.isdigit() else raw or None
