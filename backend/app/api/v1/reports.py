"""R5.7 - run a report now, export it, or put it on a schedule."""

from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit, storage
from app.db import get_session
from app.deps import Principal, current_user
from app.errors import ApiError
from app.models import ReportSchedule
from app.schemas import ReportRequest, ReportScheduleIn
from app.serialize import dump
from app.services import reports as report_service
from app.settings import settings

router = APIRouter(prefix="/reports", tags=["reports"])


@router.post("/run")
async def run_report(body: ReportRequest,
                     principal: Principal = Depends(current_user),
                     session: AsyncSession = Depends(get_session)):
    principal.require("viewer")
    report = await report_service.build(
        session, kind=body.kind, group_by=body.group_by,
        since_hours=body.since_hours, model_id=body.model_id,
    )
    if body.fmt == "json":
        return report
    if body.fmt == "csv":
        return Response(
            content=report_service.to_csv(report), media_type="text/csv",
            headers={"content-disposition":
                     f'attachment; filename="aperture-{body.kind}.csv"'},
        )
    return Response(
        content=report_service.to_pdf(report), media_type="application/pdf",
        headers={"content-disposition": f'attachment; filename="aperture-{body.kind}.pdf"'},
    )


@router.get("/schedules")
async def list_schedules(principal: Principal = Depends(current_user),
                         session: AsyncSession = Depends(get_session)) -> list[dict]:
    principal.require("viewer")
    rows = (
        await session.execute(
            sa.select(ReportSchedule)
            .order_by(ReportSchedule.created_at.desc())
        )
    ).scalars().all()
    out = []
    for row in rows:
        item = {c.name: getattr(row, c.name) for c in ReportSchedule.__table__.columns}
        if row.last_artifact_key:
            item["last_artifact_url"] = await storage.presign_get(
                settings.s3_bucket_reports, row.last_artifact_key, 3600, public=True
            )
        out.append(item)
    return out


@router.post("/schedules", status_code=201)
async def create_schedule(body: ReportScheduleIn, request: Request,
                          principal: Principal = Depends(current_user),
                          session: AsyncSession = Depends(get_session)) -> dict:
    principal.require("developer")
    if body.cron:
        _validate_cron(body.cron)
    if body.channel != "none" and not body.destination:
        raise ApiError("invalid_request", f"a {body.channel} destination is required")

    schedule = ReportSchedule(created_by=principal.id,
                              **body.model_dump())
    session.add(schedule)
    await session.flush()
    await audit.record(session, action="report.create", actor_id=principal.id,
                       actor_label=principal.label,
                       target_type="report", target_id=schedule.id, target_label=body.name,
                       payload=body.model_dump(),
                       client_ip=request.client.host if request.client else None)
    await session.commit()
    return await dump(session, schedule)


@router.post("/schedules/{schedule_id}/run")
async def run_schedule_now(schedule_id: str, principal: Principal = Depends(current_user),
                           session: AsyncSession = Depends(get_session)) -> dict:
    schedule = await session.get(ReportSchedule, schedule_id)
    if schedule is None:
        raise ApiError("not_found", "no such report schedule")
    principal.require("developer")
    from app.jobs.queue import enqueue

    await enqueue("run_report_schedule", schedule.id)
    return {"queued": True, "schedule_id": schedule.id}


@router.delete("/schedules/{schedule_id}", status_code=204)
async def delete_schedule(schedule_id: str, request: Request,
                          principal: Principal = Depends(current_user),
                          session: AsyncSession = Depends(get_session)) -> None:
    schedule = await session.get(ReportSchedule, schedule_id)
    if schedule is None:
        raise ApiError("not_found", "no such report schedule")
    principal.require("developer")
    await audit.record(session, action="report.delete", actor_id=principal.id,
                       actor_label=principal.label,
                       target_type="report", target_id=schedule.id, target_label=schedule.name,
                       client_ip=request.client.host if request.client else None)
    await session.delete(schedule)
    await session.commit()


def _validate_cron(expression: str) -> None:
    parts = expression.split()
    if len(parts) != 5:
        raise ApiError(
            "invalid_request",
            "cron needs five fields: minute hour day-of-month month day-of-week "
            "(for example '0 7 * * 1' for Mondays at 07:00 UTC)",
        )
