"""R5.9 - rolling up inference logs into comparable windows and reading the signals.

Nothing here claims to detect *concept* drift; it reports the observable things
that move when the world changes underneath a model: confidence distribution,
class mix, and the shape/brightness of the inputs themselves. Those three catch
the common real-world regressions - a new camera, a firmware update that changes
exposure, a client sending 4K where it used to send 720p.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DriftWindow, InferenceLog

CONFIDENCE_DROP = 0.05      # absolute mean-confidence delta worth surfacing
CLASS_MIX_L1 = 0.25         # total variation distance on the class distribution
RESOLUTION_SHIFT = 0.15     # relative change in mean megapixels
BRIGHTNESS_SHIFT = 0.08


async def rollup_hour(session: AsyncSession, *, hour_start: datetime) -> int:
    """Aggregate one hour of logs per version. Idempotent for that hour."""
    hour_end = hour_start + timedelta(hours=1)
    rows = (
        await session.execute(
            sa.select(
                InferenceLog.model_id, InferenceLog.version_id,
                sa.func.count().label("requests"),
                sa.func.avg(sa.cast(InferenceLog.confidence["mean"].as_float(), sa.Float))
                .label("confidence_mean"),
                sa.func.avg(InferenceLog.detection_count).label("detections_mean"),
                sa.func.avg(sa.cast(InferenceLog.input_stats["summary"]["megapixels"].as_float(),
                                    sa.Float)).label("megapixels"),
                sa.func.avg(sa.cast(InferenceLog.input_stats["summary"]["brightness"].as_float(),
                                    sa.Float)).label("brightness"),
                sa.func.avg(sa.cast(InferenceLog.input_stats["summary"]["width"].as_float(),
                                    sa.Float)).label("width"),
                sa.func.avg(sa.cast(InferenceLog.input_stats["summary"]["height"].as_float(),
                                    sa.Float)).label("height"),
                sa.func.avg(InferenceLog.total_ms).label("total_ms"),
            )
            .where(InferenceLog.created_at >= hour_start, InferenceLog.created_at < hour_end,
                   InferenceLog.status == "ok", InferenceLog.version_id.is_not(None))
            .group_by(InferenceLog.model_id, InferenceLog.version_id)
        )
    ).all()

    written = 0
    for row in rows:
        classes = await _class_histogram(session, row.version_id, hour_start, hour_end)
        stats = {
            "confidence_mean": _r(row.confidence_mean),
            "detections_mean": _r(row.detections_mean),
            "megapixels": _r(row.megapixels),
            "brightness": _r(row.brightness),
            "width": _r(row.width),
            "height": _r(row.height),
            "latency_mean_ms": _r(row.total_ms),
            "classes": classes,
        }
        existing = (
            await session.execute(
                sa.select(DriftWindow).where(DriftWindow.version_id == row.version_id,
                                             DriftWindow.window_start == hour_start)
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(DriftWindow(
                model_id=row.model_id, version_id=row.version_id,
                window_start=hour_start, window_end=hour_end, request_count=int(row.requests),
                stats=stats,
            ))
        else:
            existing.request_count = int(row.requests)
            existing.stats = stats
        written += 1
    await session.commit()
    return written


async def _class_histogram(session: AsyncSession, version_id: str, start: datetime,
                           end: datetime) -> dict[str, float]:
    """Normalised class mix from the per-request confidence summary."""
    rows = (
        await session.execute(
            sa.select(InferenceLog.confidence)
            .where(InferenceLog.version_id == version_id, InferenceLog.created_at >= start,
                   InferenceLog.created_at < end, InferenceLog.status == "ok")
            .limit(5000)
        )
    ).scalars().all()
    counts: dict[str, int] = {}
    for confidence in rows:
        for name, count in ((confidence or {}).get("classes") or {}).items():
            counts[name] = counts.get(name, 0) + int(count)
    total = sum(counts.values())
    if not total:
        return {}
    return {k: round(v / total, 5) for k, v in sorted(counts.items(), key=lambda kv: -kv[1])[:40]}


def _r(value) -> float | None:
    return round(float(value), 5) if value is not None else None


def compare_windows(baseline: dict | None, current: dict | None) -> list[dict]:
    """Turn two windows into human-readable signals with a severity."""
    if not baseline or not current:
        return [{
            "signal": "baseline_missing",
            "severity": "info",
            "message": "Set a baseline window to start comparing. Pick a period you are happy "
                       "with - drift is only meaningful against a reference you trust.",
        }]

    signals: list[dict] = []

    b_conf, c_conf = baseline.get("confidence_mean"), current.get("confidence_mean")
    if b_conf and c_conf:
        delta = c_conf - b_conf
        if abs(delta) >= CONFIDENCE_DROP:
            signals.append({
                "signal": "confidence_shift",
                "severity": "warning" if delta < 0 else "info",
                "baseline": b_conf, "current": c_conf, "delta": round(delta, 4),
                "message": (
                    f"Mean confidence {'fell' if delta < 0 else 'rose'} from {b_conf:.3f} to "
                    f"{c_conf:.3f}."
                ),
            })

    b_classes, c_classes = baseline.get("classes") or {}, current.get("classes") or {}
    if b_classes and c_classes:
        keys = set(b_classes) | set(c_classes)
        l1 = sum(abs(c_classes.get(k, 0.0) - b_classes.get(k, 0.0)) for k in keys) / 2
        if l1 >= CLASS_MIX_L1:
            moved = sorted(
                ((k, round(c_classes.get(k, 0.0) - b_classes.get(k, 0.0), 4)) for k in keys),
                key=lambda kv: -abs(kv[1]),
            )[:5]
            signals.append({
                "signal": "class_mix_shift", "severity": "warning",
                "distance": round(l1, 4), "top_changes": dict(moved),
                "message": (
                    f"The predicted class mix moved by {l1:.0%}; biggest change: "
                    f"{moved[0][0]} {moved[0][1]:+.1%}."
                ),
            })

    for key, threshold, label in (
        ("megapixels", RESOLUTION_SHIFT, "input resolution"),
        ("brightness", BRIGHTNESS_SHIFT, "input brightness"),
    ):
        b_val, c_val = baseline.get(key), current.get(key)
        if b_val and c_val:
            rel = (c_val - b_val) / b_val if key == "megapixels" else (c_val - b_val)
            if abs(rel) >= threshold:
                signals.append({
                    "signal": f"{key}_shift", "severity": "warning",
                    "baseline": b_val, "current": c_val, "delta": round(rel, 4),
                    "message": (
                        f"Mean {label} changed by {rel:+.0%} - check whether a client changed "
                        "camera or capture settings."
                    ),
                })

    b_det, c_det = baseline.get("detections_mean"), current.get("detections_mean")
    if b_det and c_det and b_det > 0:
        rel = (c_det - b_det) / b_det
        if abs(rel) >= 0.3:
            signals.append({
                "signal": "detections_per_image_shift",
                "severity": "warning",
                "baseline": b_det, "current": c_det, "delta": round(rel, 4),
                "message": f"Detections per image changed by {rel:+.0%}.",
            })

    if not signals:
        signals.append({
            "signal": "stable", "severity": "ok",
            "message": "Confidence, class mix and input statistics all sit within their "
                       "thresholds of the baseline.",
        })
    return signals


def kl_divergence(p: dict[str, float], q: dict[str, float], epsilon: float = 1e-6) -> float:
    """Available for reports that want a single scalar instead of the signal list."""
    keys = set(p) | set(q)
    return sum(
        (pv := p.get(k, epsilon)) * math.log(pv / max(q.get(k, epsilon), epsilon))
        for k in keys
    )
