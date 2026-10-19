"""The inference hot path: intake -> routing -> a runtime replica -> typed response.

Design notes that matter for latency:
  * images are never decoded here; raw bytes are forwarded and the runtime (which
    must decode anyway) returns the dimensions and input statistics
  * logging and payload capture happen after the response is on the wire
  * a replica is picked by load: what its last heartbeat reported in flight and
    queued for the version, plus what this process has sent it since
  * a request is retried at most once, on a different replica, and only when
    the first one never started on it (could not connect, does not hold the
    version, or refused with queue_full). A second refusal goes back to the
    caller: backpressure is still reported, not hidden (R4.3)
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import logging
import random
import socket
import time
import uuid
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx
import sqlalchemy as sa
from aperture_core.configspec import OVERRIDABLE_THRESHOLDS
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import evict_replica, healthy_runtimes_for_version, next_replica_index
from app.errors import ApiError
from app.models import Deployment, Model, ModelVersion
from app.services.resolver import Resolved, resolve
from app.settings import settings

log = logging.getLogger("aperture.gateway")

_client: httpx.AsyncClient | None = None


def http_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=settings.gateway_connect_timeout_ms / 1000,
                read=settings.default_request_timeout_ms / 1000,
                write=30.0, pool=5.0,
            ),
            limits=httpx.Limits(max_connections=512, max_keepalive_connections=128),
        )
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


@dataclass(slots=True)
class InputImage:
    data: bytes
    name: str
    source: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


@dataclass
class Target:
    version: ModelVersion
    deployment: Deployment
    route: str  # primary | canary | shadow
    config_overrides: dict = field(default_factory=dict)


@dataclass
class RuntimeReply:
    runtime_id: str
    results: list[dict]
    latency: dict
    overlays: list[str]
    stats: list[dict]
    batch_executed: int
    retried: bool = False


# --------------------------------------------------------------------- intake


def _guard_size(total: int, count: int) -> None:
    limit = settings.max_payload_mb * 1024 * 1024
    if total > limit:
        raise ApiError(
            "payload_too_large",
            f"request payload is {total / 1e6:.1f} MB, the limit is {settings.max_payload_mb} MB",
            details={"max_payload_mb": settings.max_payload_mb},
        )
    if count > settings.max_batch_images:
        raise ApiError(
            "batch_too_large",
            f"{count} images in one request, the limit is {settings.max_batch_images}",
            details={"max_batch_images": settings.max_batch_images},
        )
    if count == 0:
        raise ApiError("invalid_request",
                       "no image supplied - use multipart 'images', 'image' (base64) or 'image_url'")


def _check_url(url: str) -> None:
    """Keep remote-URL intake from becoming an SSRF hole."""
    if not settings.allow_remote_image_urls:
        raise ApiError("invalid_request", "fetching images by URL is disabled on this instance")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ApiError("invalid_request", f"unsupported URL scheme '{parsed.scheme}'")
    host = parsed.hostname or ""
    allowlist = settings.url_allowlist
    if allowlist:
        if not any(host == entry or host.endswith("." + entry) for entry in allowlist):
            raise ApiError("invalid_request", f"host '{host}' is not in the image URL allowlist")
        return
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ApiError("invalid_request", f"could not resolve '{host}'") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ApiError(
                "invalid_request",
                f"'{host}' resolves to a private address; add it to AP_REMOTE_URL_ALLOWLIST "
                "if fetching from it is intended",
            )


async def fetch_url_image(url: str) -> InputImage:
    _check_url(url)
    limit = settings.max_payload_mb * 1024 * 1024
    try:
        async with http_client().stream("GET", url, follow_redirects=False) as response:
            if response.status_code >= 400:
                raise ApiError("invalid_image",
                               f"fetching {url} returned HTTP {response.status_code}")
            chunks, total = [], 0
            async for chunk in response.aiter_bytes(1 << 20):
                total += len(chunk)
                if total > limit:
                    raise ApiError("payload_too_large", f"remote image at {url} exceeds the limit")
                chunks.append(chunk)
    except httpx.HTTPError as exc:
        raise ApiError("invalid_image", f"could not fetch {url}: {exc}") from exc
    name = urlparse(url).path.rsplit("/", 1)[-1] or "remote"
    return InputImage(data=b"".join(chunks), name=name, source="url")


def decode_b64_input(payload: str, index: int) -> InputImage:
    raw = payload.split(",", 1)[1] if payload.startswith("data:") else payload
    try:
        data = base64.b64decode(raw, validate=True)
    except Exception as exc:
        raise ApiError("invalid_image", f"image {index} is not valid base64") from exc
    return InputImage(data=data, name=f"image_{index}", source="base64")


# -------------------------------------------------------------------- routing


def sanitise_overrides(raw: dict | None) -> dict:
    if not raw:
        return {}
    unknown = [k for k in raw if k not in OVERRIDABLE_THRESHOLDS]
    if unknown:
        raise ApiError(
            "invalid_request",
            f"these fields cannot be overridden per request: {', '.join(sorted(unknown))}",
            details={"overridable": list(OVERRIDABLE_THRESHOLDS)},
        )
    return {k: v for k, v in raw.items() if v is not None}


async def choose_targets(session: AsyncSession, model: Model, resolved: Resolved
                         ) -> tuple[Target, list[Target]]:
    """Returns (target that answers the caller, shadow targets that only mirror).

    Canary and shadow only apply when the caller asked for a *tag*. An explicit
    version number is a promise that that exact version answers (R2.7).
    """
    rows = list(
        (
            await session.execute(
                sa.select(Deployment, ModelVersion)
                .join(ModelVersion, ModelVersion.id == Deployment.version_id)
                .where(Deployment.model_id == model.id,
                       Deployment.status.in_(("live", "warming", "draining")))
            )
        ).all()
    )
    if not rows:
        raise ApiError(
            "model_not_deployed",
            f"{model.slug} has no live deployment - deploy a version first",
            details={"model": model.slug},
        )

    primary = next(
        (Target(version=v, deployment=d, route="primary")
         for d, v in rows if d.version_id == resolved.version.id and d.mode == "primary"),
        None,
    )
    if primary is None:
        raise ApiError(
            "model_not_deployed",
            f"version {resolved.version.version} of {model.slug} is not deployed"
            + (f" (tag '{resolved.tag}' points at it)" if resolved.tag else ""),
            details={"version": resolved.version.version, "tag": resolved.tag},
        )

    answering = primary
    shadows: list[Target] = []
    if resolved.tag:
        canaries = [(d, v) for d, v in rows if d.mode == "canary" and d.status == "live"]
        if canaries:
            roll = random.random() * 100
            cumulative = 0.0
            for d, v in canaries:
                cumulative += d.traffic_weight
                if roll < cumulative:
                    answering = Target(version=v, deployment=d, route="canary")
                    break
        shadows = [
            Target(version=v, deployment=d, route="shadow")
            for d, v in rows if d.mode == "shadow" and d.status == "live"
        ]
    return answering, shadows


# Requests this process has in flight per (runtime, version). The heartbeat's
# numbers are seconds old; without these, every request between two heartbeats
# would land on whichever replica looked idle at the last one.
_sent: dict[tuple[str, str], int] = {}

# Runtime answers that mean it never started on the request, so trying another
# replica cannot run the work twice.
RETRYABLE = frozenset({"no_replica_available", "queue_full"})
MIN_RETRY_BUDGET_MS = 200


def replica_load(replica: dict, version_id: str) -> int:
    reported = int((replica.get("in_flight") or {}).get(version_id, 0)) + \
        int((replica.get("queues") or {}).get(version_id, 0))
    return reported + _sent.get((replica.get("runtime_id", ""), version_id), 0)


async def pick_replica(version_id: str, model_slug: str, *,
                       exclude: frozenset[str] = frozenset()) -> dict:
    """The least-loaded replica serving this version; round-robin among equals."""
    replicas = [r for r in await healthy_runtimes_for_version(version_id)
                if r.get("runtime_id") not in exclude]
    if not replicas:
        raise ApiError(
            "no_replica_available",
            f"no healthy replica currently holds this version of {model_slug}",
            details={"version_id": version_id},
        )
    loads = [replica_load(r, version_id) for r in replicas]
    lightest = min(loads)
    candidates = sorted((r for r, load in zip(replicas, loads) if load == lightest),
                        key=lambda r: r.get("runtime_id", ""))
    index = await next_replica_index(version_id, len(candidates))
    return candidates[index % len(candidates)]


async def dispatch(version_id: str, model_slug: str, payload: dict, *, timeout_ms: int,
                   trace_headers: dict[str, str]) -> RuntimeReply:
    """Pick a replica and call it, retrying once elsewhere if it never started."""
    started = time.monotonic()
    replica = await pick_replica(version_id, model_slug)
    try:
        return await _call_tracked(replica, version_id, payload, timeout_ms=timeout_ms,
                                   trace_headers=trace_headers)
    except ApiError as exc:
        first_id = replica.get("runtime_id", "")
        connect_failed = exc.code == "upstream_error" and (exc.details or {}).get("connect_failed")
        if exc.code not in RETRYABLE and not connect_failed:
            raise
        if exc.code == "no_replica_available" or connect_failed:
            await evict_replica(version_id, first_id)
        remaining = timeout_ms - int((time.monotonic() - started) * 1000)
        if remaining < MIN_RETRY_BUDGET_MS:
            raise
        try:
            second = await pick_replica(version_id, model_slug, exclude=frozenset({first_id}))
        except ApiError:
            raise exc from None
        log.info("retrying on another replica", extra={
            "code": exc.code, "version_id": version_id, "from_runtime": first_id,
            "to_runtime": second.get("runtime_id"), "request_id": payload.get("request_id")})
        reply = await _call_tracked(second, version_id, {**payload, "deadline_ms": remaining},
                                    timeout_ms=remaining, trace_headers=trace_headers)
        reply.retried = True
        return reply


async def _call_tracked(replica: dict, version_id: str, payload: dict, *, timeout_ms: int,
                        trace_headers: dict[str, str]) -> RuntimeReply:
    key = (replica.get("runtime_id", ""), version_id)
    _sent[key] = _sent.get(key, 0) + 1
    try:
        return await call_runtime(replica, payload, timeout_ms=timeout_ms,
                                  trace_headers=trace_headers)
    finally:
        left = _sent.get(key, 1) - 1
        if left > 0:
            _sent[key] = left
        else:
            _sent.pop(key, None)


# ------------------------------------------------------------------- dispatch


async def call_runtime(replica: dict, payload: dict, *, timeout_ms: int,
                       trace_headers: dict[str, str]) -> RuntimeReply:
    url = replica["url"].rstrip("/") + "/v1/infer"
    try:
        response = await http_client().post(
            url, json=payload, timeout=httpx.Timeout(
                connect=settings.gateway_connect_timeout_ms / 1000,
                read=timeout_ms / 1000, write=30.0, pool=5.0,
            ),
            headers={"x-runtime-token": settings.runtime_internal_token, **trace_headers},
        )
    except httpx.TimeoutException as exc:
        raise ApiError(
            "timeout", f"the runtime did not answer within {timeout_ms} ms",
            details={"runtime_id": replica.get("runtime_id"), "timeout_ms": timeout_ms},
        ) from exc
    except httpx.ConnectError as exc:
        # nothing reached the runtime, so the request is safe to send elsewhere
        raise ApiError("upstream_error", f"could not reach the runtime: {exc}",
                       details={"runtime_id": replica.get("runtime_id"),
                                "connect_failed": True}) from exc
    except httpx.HTTPError as exc:
        # the connection broke after the request went out: it may have run
        raise ApiError("upstream_error", f"the runtime connection failed: {exc}",
                       details={"runtime_id": replica.get("runtime_id")}) from exc

    if response.status_code >= 400:
        body = {}
        try:
            body = response.json()
        except ValueError:
            pass
        err = body.get("error", {})
        code = err.get("code") or ("queue_full" if response.status_code == 503 else "inference_failed")
        raise ApiError(
            code, err.get("message") or f"the runtime returned HTTP {response.status_code}",
            details={"runtime_id": replica.get("runtime_id"), **(err.get("details") or {})},
            http_status=response.status_code if response.status_code in (429, 503, 504) else None,
        )

    body = response.json()
    return RuntimeReply(
        runtime_id=body.get("runtime_id") or replica.get("runtime_id", ""),
        results=body.get("results", []),
        latency=body.get("latency", {}),
        overlays=body.get("overlays", []),
        stats=body.get("stats", []),
        batch_executed=body.get("batch_executed", 0),
    )


def build_runtime_payload(target: Target, images: list[InputImage], *, request_id: str,
                          overrides: dict, want_overlay: bool, extra: dict | None,
                          deadline_ms: int, inputs: dict | None = None) -> dict:
    return {
        "request_id": request_id,
        "version_id": target.version.id,
        "images": [
            {"b64": base64.b64encode(img.data).decode(), "name": img.name} for img in images
        ],
        "inputs": inputs or {},
        "overrides": overrides,
        "want_overlay": want_overlay,
        "want_stats": True,
        "extra": extra or {},
        "deadline_ms": deadline_ms,
    }


async def mirror_to_shadow(shadows: list[Target], images: list[InputImage], *, request_id: str,
                           overrides: dict, trace_headers: dict[str, str]) -> None:
    """R2.7 - shadow traffic: run it, keep the telemetry, discard the answer."""
    for target in shadows:
        try:
            payload = build_runtime_payload(
                target, images, request_id=f"{request_id}-shadow", overrides=overrides,
                want_overlay=False, extra=None,
                deadline_ms=target.deployment.request_timeout_ms,
            )
            await dispatch(target.version.id, "shadow", payload,
                           timeout_ms=target.deployment.request_timeout_ms,
                           trace_headers=trace_headers)
        except ApiError as exc:
            log.info("shadow request failed", extra={"code": exc.code, "request_id": request_id})
        except Exception:
            log.warning("shadow request errored", exc_info=True)


def new_request_id() -> str:
    return uuid.uuid4().hex


async def resolve_target(session: AsyncSession, model: Model, ref: str
                         ) -> tuple[Resolved, Target, list[Target]]:
    resolved = await resolve(session, model, ref)
    if resolved.version.status in ("draft", "validating", "failed"):
        raise ApiError(
            "model_not_deployed",
            f"version {resolved.version.version} is '{resolved.version.status}' and cannot serve "
            "traffic yet",
        )
    target, shadows = await choose_targets(session, model, resolved)
    return resolved, target, shadows


class Stopwatch:
    def __init__(self) -> None:
        self.t0 = time.perf_counter()

    def ms(self) -> float:
        return (time.perf_counter() - self.t0) * 1000
