"""Queries behind the metrics dashboard (R5.4), the explorer (R5.6) and reports (R5.7).

Everything is one pass over inference_logs with Postgres doing the aggregation -
percentiles included - so a dashboard stays cheap as the table grows.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ApiKey, InferenceLog, Model, ModelVersion
from app.scope import models_in

BUCKET_FOR_WINDOW = {  # window minutes -> bucket width
    60: "minute",
    24 * 60: "hour",
    7 * 24 * 60: "hour",
    30 * 24 * 60: "day",
}


def bucket_for(minutes: int) -> str:
    for limit, unit in sorted(BUCKET_FOR_WINDOW.items()):
        if minutes <= limit:
            return unit
    return "day"


def _p(fraction: float):
    return sa.func.percentile_cont(fraction).within_group(InferenceLog.total_ms.asc())


def base_filters(
    *, since: datetime, model_id: str | None = None,
    version_id: str | None = None, route: str | None = None,
    workspace_id: str | None = None,
) -> list:
    clauses = [InferenceLog.created_at >= since]
    if workspace_id:
        clauses.append(InferenceLog.model_id.in_(models_in(workspace_id)))
    if model_id:
        clauses.append(InferenceLog.model_id == model_id)
    if version_id:
        clauses.append(InferenceLog.version_id == version_id)
    if route:
        clauses.append(InferenceLog.route == route)
    return clauses


async def summary(
    session: AsyncSession, *, window_minutes: int = 1440,
    model_id: str | None = None, version_id: str | None = None,
    workspace_id: str | None = None,
) -> dict:
    since = datetime.now(UTC) - timedelta(minutes=window_minutes)
    clauses = base_filters(since=since, model_id=model_id,
                           version_id=version_id, workspace_id=workspace_id)

    row = (
        await session.execute(
            sa.select(
                sa.func.count().label("requests"),
                sa.func.coalesce(sa.func.sum(InferenceLog.batch_size), 0).label("images"),
                sa.func.coalesce(
                    sa.func.sum(sa.case((InferenceLog.status != "ok", 1), else_=0)), 0
                ).label("errors"),
                _p(0.5).label("p50"),
                _p(0.95).label("p95"),
                _p(0.99).label("p99"),
                sa.func.avg(InferenceLog.queue_ms).label("queue"),
                sa.func.avg(InferenceLog.preprocess_ms).label("pre"),
                sa.func.avg(InferenceLog.inference_ms).label("inf"),
                sa.func.avg(InferenceLog.postprocess_ms).label("post"),
                sa.func.avg(InferenceLog.gateway_ms).label("gw"),
            ).where(*clauses)
        )
    ).one()

    by_status = dict(
        (
            await session.execute(
                sa.select(InferenceLog.status, sa.func.count())
                .where(*clauses).group_by(InferenceLog.status)
            )
        ).all()
    )

    unit = bucket_for(window_minutes)
    bucket = sa.func.date_trunc(unit, InferenceLog.created_at)
    series_rows = (
        await session.execute(
            sa.select(
                bucket.label("ts"),
                sa.func.count().label("requests"),
                sa.func.coalesce(sa.func.sum(InferenceLog.batch_size), 0).label("images"),
                sa.func.coalesce(
                    sa.func.sum(sa.case((InferenceLog.status != "ok", 1), else_=0)), 0
                ).label("errors"),
                _p(0.5).label("p50"),
                _p(0.95).label("p95"),
                _p(0.99).label("p99"),
            ).where(*clauses).group_by(bucket).order_by(bucket)
        )
    ).all()

    requests = int(row.requests or 0)
    errors = int(row.errors or 0)
    seconds = max(window_minutes * 60, 1)
    return {
        "window_minutes": window_minutes,
        "requests": requests,
        "errors": errors,
        "error_rate": round(errors / requests, 5) if requests else 0.0,
        "images": int(row.images or 0),
        "rps": round(requests / seconds, 4),
        "p50_ms": _round(row.p50),
        "p95_ms": _round(row.p95),
        "p99_ms": _round(row.p99),
        "stages": {
            "queue_ms": _round(row.queue) or 0.0,
            "preprocess_ms": _round(row.pre) or 0.0,
            "inference_ms": _round(row.inf) or 0.0,
            "postprocess_ms": _round(row.post) or 0.0,
            "gateway_ms": _round(row.gw) or 0.0,
        },
        "by_status": {k: int(v) for k, v in by_status.items()},
        "series": [
            {
                "ts": r.ts, "requests": int(r.requests), "errors": int(r.errors),
                "images": int(r.images), "p50_ms": _round(r.p50), "p95_ms": _round(r.p95),
                "p99_ms": _round(r.p99),
            }
            for r in series_rows
        ],
    }


def _round(value) -> float | None:
    return round(float(value), 2) if value is not None else None


async def usage_rows(
    session: AsyncSession, *, since_hours: int, group_by: str,
    model_id: str | None = None,
) -> list[dict]:
    since = datetime.now(UTC) - timedelta(hours=since_hours)
    clauses = base_filters(since=since, model_id=model_id)

    if group_by == "model":
        key_col, label_col = InferenceLog.model_id, InferenceLog.model_slug
    elif group_by == "version":
        key_col = InferenceLog.version_id
        label_col = sa.func.concat(InferenceLog.model_slug, sa.text("' v'"),
                                   sa.cast(InferenceLog.version_number, sa.String))
    elif group_by == "consumer":
        key_col, label_col = InferenceLog.caller_id, InferenceLog.caller_label
    elif group_by == "error_code":
        key_col = sa.func.coalesce(InferenceLog.error_code, sa.text("'none'"))
        label_col = key_col
    else:  # day
        key_col = sa.cast(sa.func.date_trunc("day", InferenceLog.created_at), sa.String)
        label_col = key_col

    rows = (
        await session.execute(
            sa.select(
                key_col.label("key"),
                sa.func.min(label_col).label("label"),
                sa.func.count().label("requests"),
                sa.func.coalesce(sa.func.sum(InferenceLog.batch_size), 0).label("images"),
                sa.func.coalesce(
                    sa.func.sum(sa.case((InferenceLog.status != "ok", 1), else_=0)), 0
                ).label("errors"),
                _p(0.95).label("p95"),
                sa.func.avg(InferenceLog.total_ms).label("avg"),
            ).where(*clauses).group_by(key_col).order_by(sa.desc("requests")).limit(500)
        )
    ).all()

    out = [
        {
            "key": str(r.key) if r.key is not None else "unknown",
            "label": str(r.label or r.key or "unknown"),
            "requests": int(r.requests),
            "images": int(r.images),
            "errors": int(r.errors),
            "error_rate": round(int(r.errors) / int(r.requests), 5) if r.requests else 0.0,
            "p95_ms": _round(r.p95),
            "avg_ms": _round(r.avg),
        }
        for r in rows
    ]

    # A slug is unique at any one time, but a *deleted and recreated* model reuses it,
    # so grouping by id can produce several rows with the same label. Reading four
    # identical rows and guessing which is which is worse than a short suffix.
    seen: dict[str, int] = {}
    for row in out:
        seen[row["label"]] = seen.get(row["label"], 0) + 1
    for row in out:
        if seen[row["label"]] > 1 and row["key"] != row["label"]:
            row["label"] = f"{row['label']} · {row['key'][:6]}"
    return out


async def model_cards(session: AsyncSession, *, model_ids: list[str],
                      hours: int = 24) -> dict[str, dict]:
    """The per-model traffic figures the catalog shows next to each row."""
    if not model_ids:
        return {}
    since = datetime.now(UTC) - timedelta(hours=hours)
    rows = (
        await session.execute(
            sa.select(
                InferenceLog.model_id,
                sa.func.count().label("requests"),
                sa.func.coalesce(
                    sa.func.sum(sa.case((InferenceLog.status != "ok", 1), else_=0)), 0
                ).label("errors"),
                _p(0.95).label("p95"),
            )
            .where(InferenceLog.created_at >= since,
                   InferenceLog.model_id.in_(model_ids))
            .group_by(InferenceLog.model_id)
        )
    ).all()
    return {
        r.model_id: {
            "requests_24h": int(r.requests),
            "error_rate_24h": round(int(r.errors) / int(r.requests), 5) if r.requests else 0.0,
            "p95_ms_24h": _round(r.p95),
        }
        for r in rows
    }


async def version_traffic(session: AsyncSession, version_ids: list[str], hours: int = 24
                          ) -> dict[str, dict]:
    if not version_ids:
        return {}
    since = datetime.now(UTC) - timedelta(hours=hours)
    rows = (
        await session.execute(
            sa.select(
                InferenceLog.version_id,
                sa.func.count().label("requests"),
                sa.func.coalesce(
                    sa.func.sum(sa.case((InferenceLog.status != "ok", 1), else_=0)), 0
                ).label("errors"),
                _p(0.5).label("p50"),
                _p(0.95).label("p95"),
                sa.func.avg(InferenceLog.inference_ms).label("inference"),
            )
            .where(InferenceLog.created_at >= since, InferenceLog.version_id.in_(version_ids))
            .group_by(InferenceLog.version_id)
        )
    ).all()
    return {
        r.version_id: {
            "requests": int(r.requests),
            "error_rate": round(int(r.errors) / int(r.requests), 5) if r.requests else 0.0,
            "p50_ms": _round(r.p50), "p95_ms": _round(r.p95),
            "inference_ms": _round(r.inference),
        }
        for r in rows
    }


async def consumer_labels(session: AsyncSession, caller_ids: list[str]) -> dict[str, str]:
    if not caller_ids:
        return {}
    rows = (
        await session.execute(
            sa.select(ApiKey.id, ApiKey.name).where(ApiKey.id.in_(caller_ids))
        )
    ).all()
    return {k: v for k, v in rows}


async def error_breakdown(session: AsyncSession, *, since_hours: int,
                          model_id: str | None = None,
                          workspace_id: str | None = None) -> list[dict]:
    since = datetime.now(UTC) - timedelta(hours=since_hours)
    clauses = base_filters(since=since, model_id=model_id, workspace_id=workspace_id)
    rows = (
        await session.execute(
            sa.select(
                InferenceLog.error_code,
                sa.func.count().label("count"),
                sa.func.max(InferenceLog.created_at).label("last_seen"),
                sa.func.min(InferenceLog.error_message).label("sample"),
            )
            .where(*clauses, InferenceLog.status != "ok")
            .group_by(InferenceLog.error_code).order_by(sa.desc("count"))
        )
    ).all()
    return [
        {"error_code": r.error_code or "unknown", "count": int(r.count),
         "last_seen": r.last_seen, "sample_message": r.sample}
        for r in rows
    ]


async def model_and_version_names(session: AsyncSession, version_ids: list[str]
                                  ) -> dict[str, tuple[str, int]]:
    if not version_ids:
        return {}
    rows = (
        await session.execute(
            sa.select(ModelVersion.id, Model.slug, ModelVersion.version)
            .join(Model, Model.id == ModelVersion.model_id)
            .where(ModelVersion.id.in_(version_ids))
        )
    ).all()
    return {vid: (slug, number) for vid, slug, number in rows}
