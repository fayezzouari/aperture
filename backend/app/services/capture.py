"""R5.5 / R5.6 / R7.6 - sampled payload capture and the inference log record.

Sampling is per request and decided *before* the work happens so the decision is
cheap; errors are always captured (that is when you need the image most). Every
stored object carries a purge_after stamp, which is what makes retention and
hard delete a background sweep rather than a manual chore.
"""

from __future__ import annotations

import base64
import json
import logging
import random
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app import storage
from app.cache import publish_log
from app.models import InstanceSettings
from app.settings import settings

log = logging.getLogger("aperture.capture")


@dataclass(frozen=True)
class CapturePolicy:
    """The instance's overrides of the AP_CAPTURE_* defaults; None means 'use the default'."""

    sample_rate: float | None = None
    retention_days: int | None = None


POLICY_TTL_S = 10.0
_policy_cache: tuple[float, CapturePolicy] | None = None


async def load_policy(session: AsyncSession) -> CapturePolicy:
    """Read the instance settings row, cached briefly: it is consulted on every prediction."""
    global _policy_cache
    now = time.monotonic()
    if _policy_cache is not None and now - _policy_cache[0] < POLICY_TTL_S:
        return _policy_cache[1]
    row = await session.get(InstanceSettings, 1)
    policy = CapturePolicy(row.capture_sample_rate, row.capture_retention_days) if row \
        else CapturePolicy()
    _policy_cache = (now, policy)
    return policy


def forget_policy() -> None:
    """Drop the cached policy so a settings change applies on the next request."""
    global _policy_cache
    _policy_cache = None


def should_capture(*, override_rate: float | None, forced: bool | None, is_error: bool) -> bool:
    if forced is not None:
        return forced
    if is_error and settings.capture_errors_always:
        return True
    rate = effective_rate(override_rate)
    return rate > 0 and random.random() < rate


def effective_rate(override_rate: float | None) -> float:
    return settings.capture_sample_rate if override_rate is None else override_rate


def retention_days(override_days: int | None) -> int:
    return override_days or settings.capture_retention_days


async def store_payloads(
    *, model_slug: str, request_id: str,
    images: list[tuple[bytes, str]], overlays: list[str],
) -> dict:
    """Upload the request's images (and any overlay) and return their keys."""
    stored: dict = {"images": [], "overlays": [], "bucket": settings.s3_bucket_captures}
    for index, (data, name) in enumerate(images):
        ext = _extension(name, data)
        key = storage.capture_key(model_slug, request_id, index, ext)
        try:
            await storage.put_bytes(settings.s3_bucket_captures, key, data,
                                    content_type=f"image/{'jpeg' if ext == 'jpg' else ext}")
            stored["images"].append({"key": key, "name": name, "bytes": len(data)})
        except Exception:
            log.warning("capture upload failed", extra={"request_id": request_id}, exc_info=True)

    for index, overlay_b64 in enumerate(overlays):
        try:
            data = base64.b64decode(overlay_b64)
        except Exception:
            continue
        key = storage.capture_key(model_slug, request_id, index, "overlay.png")
        try:
            await storage.put_bytes(settings.s3_bucket_captures, key, data, content_type="image/png")
            stored["overlays"].append({"key": key})
        except Exception:
            log.warning("overlay upload failed", extra={"request_id": request_id}, exc_info=True)
    return stored


def _extension(name: str, data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    lower = name.lower()
    for ext in ("jpg", "jpeg", "png", "webp", "bmp", "tif", "tiff"):
        if lower.endswith(ext):
            return "jpg" if ext == "jpeg" else ext
    return "bin"


def truncate_output(results: list[dict], limit: int | None = None) -> dict:
    """Keep the log row small: masks and embeddings are summarised, not stored inline."""
    limit = limit or settings.log_output_max_bytes
    lean = []
    for result in results:
        entry = {k: v for k, v in result.items() if k not in ("segments", "raw")}
        for detection in (entry.get("detections") or []):
            if isinstance(detection, dict) and detection.get("mask"):
                mask = detection["mask"]
                detection["mask"] = {
                    "format": mask.get("format"), "size": mask.get("size"),
                    "area": mask.get("area"), "elided": True,
                }
        if entry.get("embedding"):
            vector = entry["embedding"]
            entry["embedding"] = vector[:16]
            entry["embedding_elided"] = len(vector) > 16
        lean.append(entry)

    payload = {"results": lean}
    blob = json.dumps(payload, default=str)
    if len(blob) > limit:
        return {
            "truncated": True,
            "reason": f"output exceeded {limit} bytes; the full payload is in the capture bucket",
            "counts": [
                {"index": r.get("index"),
                 "detections": len(r.get("detections") or []),
                 "classifications": len(r.get("classifications") or []),
                 "texts": len(r.get("texts") or [])}
                for r in results
            ],
        }
    return payload


def build_record(
    *, request_id: str, trace_id: str | None, span_id: str | None, model, version,
    tag: str | None, route: str, principal_kind: str,
    principal_id: str, principal_label: str, client_ip: str | None, status: str,
    http_status: int, error_code: str | None, error_message: str | None, batch_size: int,
    latency: dict, runtime_id: str | None, input_ref: dict, input_stats: dict,
    output: dict, confidence: dict, overrides: dict, capture: dict, detection_count: int,
    retention: int,
) -> dict:
    return {
        "request_id": request_id,
        "trace_id": trace_id,
        "span_id": span_id,
        "model_id": getattr(model, "id", None),
        "model_slug": getattr(model, "slug", ""),
        "version_id": getattr(version, "id", None),
        "version_number": getattr(version, "version", None),
        "tag": tag,
        "route": route,
        "caller_type": principal_kind,
        "caller_id": principal_id,
        "caller_label": principal_label,
        "client_ip": client_ip,
        "status": status,
        "http_status": http_status,
        "error_code": error_code,
        "error_message": (error_message or "")[:2000] or None,
        "batch_size": batch_size,
        "queue_ms": latency.get("queue_ms", 0.0),
        "preprocess_ms": latency.get("preprocess_ms", 0.0),
        "inference_ms": latency.get("inference_ms", 0.0),
        "postprocess_ms": latency.get("postprocess_ms", 0.0),
        "gateway_ms": latency.get("gateway_ms", 0.0),
        "total_ms": latency.get("total_ms", 0.0),
        "runtime_id": runtime_id,
        "input_ref": input_ref,
        "input_stats": input_stats,
        "output": output,
        "confidence": confidence,
        "overrides": overrides,
        "capture": capture,
        "detection_count": detection_count,
        "purge_after": (datetime.now(UTC) + timedelta(days=retention)).isoformat(),
        "created_at": datetime.now(UTC).isoformat(),
    }


async def emit(record: dict) -> None:
    await publish_log(record)
