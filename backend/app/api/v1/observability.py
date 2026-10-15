"""R5.x - the inference explorer, the metrics dashboard, drift signals and feedback."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit, storage
from app.cache import all_runtimes, stream_depth
from app.db import get_session
from app.deps import Page, Principal, current_user, pagination, project_dep
from app.errors import ApiError
from app.models import (
    AlertRule,
    Assignment,
    Deployment,
    DriftWindow,
    InferenceLog,
    Model,
    ModelVersion,
    Project,
    Runtime,
)
from app.schemas import (
    AlertRuleOut,
    DriftOut,
    FlagIn,
    InferenceLogDetail,
    InferenceLogOut,
    MetricsSummary,
    OverviewOut,
    ProjectOut,
    RuntimeOut,
)
from app.serialize import dump_loaded
from app.services import analytics
from app.settings import settings

router = APIRouter(tags=["observability"])


def _log_out(row: InferenceLog) -> InferenceLogOut:
    return InferenceLogOut.model_validate(
        dump_loaded(row, has_capture=bool((row.capture or {}).get("images")))
    )


@router.get("/inferences", response_model=list[InferenceLogOut])
async def list_inferences(
    model_id: str | None = None,
    version_id: str | None = None,
    tag: str | None = None,
    status: str | None = Query(None, description="ok | error | timeout | rejected"),
    error_code: str | None = None,
    route: str | None = None,
    caller_id: str | None = None,
    request_id: str | None = None,
    trace_id: str | None = None,
    min_latency_ms: float | None = None,
    max_latency_ms: float | None = None,
    min_confidence: float | None = Query(None, ge=0, le=1),
    max_confidence: float | None = Query(None, ge=0, le=1),
    flagged: bool | None = None,
    has_capture: bool | None = None,
    since_hours: int = Query(24, ge=1, le=24 * 400),
    page: Page = Depends(pagination),
    project: Project = Depends(project_dep),
    principal: Principal = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[InferenceLogOut]:
    """R5.6 - the explorer's filter surface."""
    principal.require(project.id, "viewer")
    since = datetime.now(UTC) - timedelta(hours=since_hours)
    stmt = sa.select(InferenceLog).where(InferenceLog.project_id == project.id,
                                         InferenceLog.created_at >= since)
    if model_id:
        stmt = stmt.where(InferenceLog.model_id == model_id)
    if version_id:
        stmt = stmt.where(InferenceLog.version_id == version_id)
    if tag:
        stmt = stmt.where(InferenceLog.tag == tag)
    if status:
        stmt = stmt.where(InferenceLog.status == status)
    if error_code:
        stmt = stmt.where(InferenceLog.error_code == error_code)
    if route:
        stmt = stmt.where(InferenceLog.route == route)
    if caller_id:
        stmt = stmt.where(InferenceLog.caller_id == caller_id)
    if request_id:
        stmt = stmt.where(InferenceLog.request_id == request_id)
    if trace_id:
        stmt = stmt.where(InferenceLog.trace_id == trace_id)
    if min_latency_ms is not None:
        stmt = stmt.where(InferenceLog.total_ms >= min_latency_ms)
    if max_latency_ms is not None:
        stmt = stmt.where(InferenceLog.total_ms <= max_latency_ms)
    if flagged is not None:
        stmt = stmt.where(InferenceLog.flagged.is_(flagged))
    if min_confidence is not None:
        stmt = stmt.where(
            sa.cast(InferenceLog.confidence["mean"].as_float(), sa.Float) >= min_confidence
        )
    if max_confidence is not None:
        stmt = stmt.where(
            sa.cast(InferenceLog.confidence["mean"].as_float(), sa.Float) <= max_confidence
        )
    if has_capture is not None:
        clause = sa.func.jsonb_array_length(InferenceLog.capture["images"]) > 0
        stmt = stmt.where(clause if has_capture else sa.not_(clause))

    rows = (
        await session.execute(
            stmt.order_by(InferenceLog.created_at.desc()).limit(page.limit).offset(page.offset)
        )
    ).scalars().all()
    return [_log_out(r) for r in rows]


@router.get("/inferences/{log_id}", response_model=InferenceLogDetail)
async def inference_detail(log_id: str, project: Project = Depends(project_dep),
                           principal: Principal = Depends(current_user),
                           session: AsyncSession = Depends(get_session)) -> InferenceLogDetail:
    principal.require(project.id, "viewer")
    row = await session.get(InferenceLog, log_id)
    if row is None or row.project_id != project.id:
        raise ApiError("not_found", "no such inference record")

    capture = row.capture or {}
    images, overlays = [], []
    for item in capture.get("images", [])[:16]:
        images.append(
            await storage.presign_get(settings.s3_bucket_captures, item["key"], 3600, public=True)
        )
    for item in capture.get("overlays", [])[:16]:
        overlays.append(
            await storage.presign_get(settings.s3_bucket_captures, item["key"], 3600, public=True)
        )
    return InferenceLogDetail.model_validate(
        dump_loaded(row, has_capture=bool(capture.get("images")), image_urls=images,
                    overlay_urls=overlays)
    )


@router.post("/inferences/{log_id}/flag", response_model=InferenceLogOut)
async def flag_inference(log_id: str, body: FlagIn, request: Request,
                         project: Project = Depends(project_dep),
                         principal: Principal = Depends(current_user),
                         session: AsyncSession = Depends(get_session)) -> InferenceLogOut:
    """R5.10 - flagging feeds the retraining export."""
    principal.require(project.id, "developer")
    row = await session.get(InferenceLog, log_id)
    if row is None or row.project_id != project.id:
        raise ApiError("not_found", "no such inference record")
    if body.flagged and not (row.capture or {}).get("images"):
        raise ApiError(
            "conflict",
            "this request was not captured, so there is no image to retrain on - raise the "
            "capture sample rate for this project",
        )
    row.flagged = body.flagged
    row.flag_label = body.label
    row.flag_note = body.note
    row.flagged_by = principal.id
    await audit.record(session, action="prediction.flag", project_id=project.id,
                       actor_id=principal.id, actor_label=principal.label,
                       target_type="inference", target_id=row.id, target_label=row.request_id,
                       payload={"flagged": body.flagged, "label": body.label},
                       client_ip=request.client.host if request.client else None)
    await session.commit()
    return _log_out(row)


@router.get("/metrics/summary", response_model=MetricsSummary)
async def metrics_summary(
    window_minutes: int = Query(1440, ge=5, le=60 * 24 * 90),
    model_id: str | None = None,
    version_id: str | None = None,
    project: Project = Depends(project_dep),
    principal: Principal = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> MetricsSummary:
    """R5.4 - the numbers behind the dashboard, per model and version."""
    principal.require(project.id, "viewer")
    data = await analytics.summary(session, project_id=project.id,
                                   window_minutes=window_minutes, model_id=model_id,
                                   version_id=version_id)
    return MetricsSummary.model_validate(data)


@router.get("/metrics/errors")
async def error_breakdown(since_hours: int = Query(24, ge=1, le=24 * 90),
                          model_id: str | None = None,
                          project: Project = Depends(project_dep),
                          principal: Principal = Depends(current_user),
                          session: AsyncSession = Depends(get_session)) -> list[dict]:
    principal.require(project.id, "viewer")
    return await analytics.error_breakdown(session, project_id=project.id,
                                           since_hours=since_hours, model_id=model_id)


@router.get("/drift/{version_id}", response_model=DriftOut)
async def drift(version_id: str, windows: int = Query(48, ge=2, le=720),
                project: Project = Depends(project_dep),
                principal: Principal = Depends(current_user),
                session: AsyncSession = Depends(get_session)) -> DriftOut:
    """R5.9 - confidence, class mix and input statistics against the baseline window."""
    principal.require(project.id, "viewer")
    rows = list(
        (
            await session.execute(
                sa.select(DriftWindow)
                .where(DriftWindow.version_id == version_id,
                       DriftWindow.project_id == project.id)
                .order_by(DriftWindow.window_start.desc()).limit(windows)
            )
        ).scalars().all()
    )
    baseline = next((r for r in rows if r.is_baseline), None)
    if baseline is None:
        baseline = (
            await session.execute(
                sa.select(DriftWindow)
                .where(DriftWindow.version_id == version_id,
                       DriftWindow.project_id == project.id,
                       DriftWindow.is_baseline.is_(True))
                .order_by(DriftWindow.window_start.desc()).limit(1)
            )
        ).scalar_one_or_none()
    current = rows[0] if rows else None

    from app.services.drift import compare_windows

    signals = compare_windows(baseline.stats if baseline else None,
                              current.stats if current else None)
    return DriftOut(
        version_id=version_id,
        baseline={"window_start": baseline.window_start, "stats": baseline.stats,
                  "requests": baseline.request_count} if baseline else None,
        current={"window_start": current.window_start, "stats": current.stats,
                 "requests": current.request_count} if current else None,
        windows=[
            {"window_start": r.window_start, "requests": r.request_count,
             "stats": r.stats, "is_baseline": r.is_baseline}
            for r in reversed(rows)
        ],
        signals=signals,
    )


@router.post("/drift/{version_id}/baseline")
async def set_baseline(version_id: str, window_start: datetime | None = None,
                       project: Project = Depends(project_dep),
                       principal: Principal = Depends(current_user),
                       session: AsyncSession = Depends(get_session)) -> dict:
    principal.require(project.id, "developer")
    await session.execute(
        sa.update(DriftWindow)
        .where(DriftWindow.version_id == version_id, DriftWindow.project_id == project.id)
        .values(is_baseline=False)
    )
    stmt = sa.select(DriftWindow).where(DriftWindow.version_id == version_id,
                                        DriftWindow.project_id == project.id)
    if window_start:
        stmt = stmt.where(DriftWindow.window_start == window_start)
    target = (
        await session.execute(stmt.order_by(DriftWindow.window_start.desc()).limit(1))
    ).scalar_one_or_none()
    if target is None:
        raise ApiError("not_found", "there is no rolled-up window to use as a baseline yet")
    target.is_baseline = True
    await session.commit()
    return {"version_id": version_id, "baseline_window_start": target.window_start}


@router.get("/overview", response_model=OverviewOut)
async def overview(project: Project = Depends(project_dep),
                   principal: Principal = Depends(current_user),
                   session: AsyncSession = Depends(get_session)) -> OverviewOut:
    """What the home screen shows: fleet health, traffic, and what is on fire."""
    principal.require(project.id, "viewer")

    models = (
        await session.execute(
            sa.select(sa.func.count()).select_from(Model)
            .where(Model.project_id == project.id, Model.archived_at.is_(None))
        )
    ).scalar_one()
    versions = (
        await session.execute(
            sa.select(sa.func.count()).select_from(ModelVersion)
            .join(Model, Model.id == ModelVersion.model_id)
            .where(Model.project_id == project.id)
        )
    ).scalar_one()
    deployments = list(
        (
            await session.execute(
                sa.select(Deployment).where(Deployment.project_id == project.id,
                                            Deployment.status != "stopped")
            )
        ).scalars().all()
    )
    ready = 0
    if deployments:
        ready = int(
            (
                await session.execute(
                    sa.select(sa.func.count()).select_from(Assignment)
                    .where(Assignment.deployment_id.in_([d.id for d in deployments]),
                           Assignment.state == "ready")
                )
            ).scalar_one()
        )

    runtime_rows = list(
        (
            await session.execute(
                sa.select(Runtime).order_by(Runtime.last_heartbeat.desc().nullslast())
            )
        ).scalars().all()
    )
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.runtime_heartbeat_ttl_s)
    live_ids = {r["runtime_id"] for r in await all_runtimes()}
    runtimes = [
        RuntimeOut.model_validate(
            dump_loaded(r, fresh=bool(r.last_heartbeat and r.last_heartbeat >= cutoff)
                        or r.id in live_ids)
        )
        for r in runtime_rows
    ]

    metrics = await analytics.summary(session, project_id=project.id, window_minutes=1440)
    recent = (
        await session.execute(
            sa.select(InferenceLog)
            .where(InferenceLog.project_id == project.id, InferenceLog.status != "ok")
            .order_by(InferenceLog.created_at.desc()).limit(10)
        )
    ).scalars().all()
    firing = (
        await session.execute(
            sa.select(AlertRule).where(AlertRule.project_id == project.id,
                                       AlertRule.state == "firing")
        )
    ).scalars().all()

    return OverviewOut(
        project=ProjectOut.model_validate(
            dump_loaded(project, model_count=int(models), role=principal.roles.get(project.id))
        ),
        models=int(models), versions=int(versions), deployments=len(deployments),
        replicas_ready=ready,
        replicas_desired=sum(d.replicas for d in deployments),
        runtimes=runtimes,
        metrics=MetricsSummary.model_validate(metrics),
        recent_errors=[_log_out(r) for r in recent],
        firing_alerts=[AlertRuleOut.model_validate(r) for r in firing],
        log_stream_depth=await stream_depth(),
    )
