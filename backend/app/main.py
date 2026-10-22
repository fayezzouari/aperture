"""Aperture API: model registry, inference gateway and observability in one process.

The gateway and the control plane share a process on purpose: tag resolution
needs the registry, and keeping them together removes a network hop from the
inference path. The *runtimes* are separate - that is where the GPU work and the
version-specific dependencies live.
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from aperture_core.results import ErrorResponse
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app import bootstrap, storage, telemetry
from app.api.compat.roboflow import router as roboflow_compat_router
from app.api.internal import router as internal_router
from app.api.oauth import well_known as oauth_well_known
from app.api.router import v1
from app.cache import client as redis_client
from app.cache import close as close_redis
from app.db import SessionLocal, create_schema, engine
from app.errors import install_handlers
from app.logging_setup import configure_logging, request_id_var, trace_id_var
from app.metrics import REGISTRY
from app.schemas import PresetOut  # noqa: F401  (keeps the schema in the OpenAPI document)
from app.services.gateway import close_client
from app.settings import settings

log = logging.getLogger("aperture.api")

DESCRIPTION = """
Self-hosted serving, versioning and observability for computer-vision models.

**Calling a model**

    POST /v1/models/{model}/{version|tag}/predict
    x-api-key: ap_xxxx.yyyy

`{version|tag}` accepts a version number (`7`, `v7`) or a movable tag
(`production`, `staging`). Tags resolve per request, so a rollback is a retag -
no redeploy, no client change.

Every response carries `request_id`, the resolved `model_version` and a
per-stage latency breakdown. Every error is
`{"error": {"code", "message", "request_id"}}` with a stable `code`.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.log_level)
    log.info("starting api", extra={"env": settings.env, "instance": settings.instance_name})

    await create_schema()
    try:
        await storage.ensure_buckets()
    except Exception:
        log.exception("object storage is not reachable yet - uploads will fail until it is")

    async with SessionLocal() as session:
        await bootstrap.seed(session)
        await bootstrap.retire_sample_validations(session)

    telemetry.setup_telemetry(app, engine)
    try:
        await redis_client().ping()
    except Exception:
        log.exception("redis is not reachable - routing and rate limits will fail")

    yield

    await close_client()
    await close_redis()
    await engine.dispose()
    log.info("api stopped")


app = FastAPI(
    title="Aperture",
    version="0.1.0",
    description=DESCRIPTION,
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
    responses={
        400: {"model": ErrorResponse}, 401: {"model": ErrorResponse},
        403: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
        409: {"model": ErrorResponse}, 413: {"model": ErrorResponse},
        422: {"model": ErrorResponse}, 429: {"model": ErrorResponse},
        500: {"model": ErrorResponse}, 503: {"model": ErrorResponse},
    },
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["x-request-id", "x-trace-id", "x-aperture-model-version",
                    "x-aperture-route", "x-ratelimit-remaining"],
)

install_handlers(app)
app.include_router(v1)
# OAuth metadata sits at the origin's root, where RFC 8414 says clients look
app.include_router(oauth_well_known)
app.include_router(internal_router)
# Roboflow's contract, not Aperture's, so it sits outside /v1. The route
# itself 404s unless AP_ROBOFLOW_COMPAT_ENABLED is set.
app.include_router(roboflow_compat_router)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """One request id for the whole hop, echoed in logs, traces and the response."""
    incoming = request.headers.get("x-request-id")
    request_id = incoming if incoming and len(incoming) <= 64 else uuid.uuid4().hex
    request.state.request_id = request_id
    request_id_var.set(request_id)

    trace_id, _ = telemetry.current_ids()
    if not trace_id:
        traceparent = request.headers.get("traceparent", "")
        parts = traceparent.split("-")
        trace_id = parts[1] if len(parts) >= 3 else None
    trace_id_var.set(trace_id)

    started = time.perf_counter()
    try:
        response = await call_next(request)
    finally:
        elapsed = (time.perf_counter() - started) * 1000
        if not request.url.path.startswith(("/metrics", "/healthz", "/readyz")):
            log.info(
                "request",
                extra={"method": request.method, "path": request.url.path,
                       "duration_ms": round(elapsed, 2)},
            )
    response.headers["x-request-id"] = request_id
    response.headers["server-timing"] = f"total;dur={elapsed:.1f}"
    return response


@app.get("/healthz", include_in_schema=False)
async def healthz() -> PlainTextResponse:
    return PlainTextResponse("ok")


@app.get("/readyz", include_in_schema=False)
async def readyz() -> JSONResponse:
    """Ready means: database reachable, redis reachable, buckets present."""
    import sqlalchemy as sa

    checks: dict[str, str] = {}
    ok = True
    try:
        async with SessionLocal() as session:
            await session.execute(sa.text("select 1"))
        checks["postgres"] = "ok"
    except Exception as exc:
        checks["postgres"] = f"error: {exc}"[:200]
        ok = False
    try:
        await redis_client().ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {exc}"[:200]
        ok = False
    try:
        await storage.head(settings.s3_bucket_weights, "__probe__")
        checks["object_storage"] = "ok"
    except Exception as exc:
        checks["object_storage"] = f"error: {exc}"[:200]
        ok = False
    return JSONResponse({"ready": ok, "checks": checks}, status_code=200 if ok else 503)


@app.get("/metrics", include_in_schema=False)
async def metrics() -> PlainTextResponse:
    return PlainTextResponse(generate_latest(REGISTRY).decode(), media_type=CONTENT_TYPE_LATEST)


@app.get("/v1/meta/instance", tags=["meta"])
async def instance() -> dict:
    """What the UI needs to render itself without hard-coding server limits."""
    return {
        "instance_name": settings.instance_name,
        "env": settings.env,
        "version": app.version,
        "limits": {
            "max_payload_mb": settings.max_payload_mb,
            "max_batch_images": settings.max_batch_images,
            "max_upload_mb": settings.max_upload_mb,
            "upload_chunk_mb": settings.upload_chunk_mb,
        },
        "capture": {
            "default_sample_rate": settings.capture_sample_rate,
            "retention_days": settings.capture_retention_days,
        },
        "features": {
            "oidc": settings.oidc_enabled,
            "ldap": settings.ldap_enabled,
            "tracing": settings.otel_enabled,
            "remote_image_urls": settings.allow_remote_image_urls,
            # whether an import can run without the caller supplying a key of
            # their own - never the key itself
            "roboflow_import": bool(settings.roboflow_api_key),
            "roboflow_compat": settings.roboflow_compat_enabled,
        },
    }
