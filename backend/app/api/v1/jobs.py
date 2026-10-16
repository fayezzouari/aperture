"""R3.8 - async inference jobs, plus the operational jobs (exports, purges)."""

from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit, storage
from app.db import get_session
from app.deps import Page, Principal, current_user, get_model_or_404, pagination
from app.errors import ApiError
from app.jobs.queue import submit
from app.models import Job
from app.schemas import AsyncJobIn, JobOut
from app.settings import settings

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=list[JobOut])
async def list_jobs(kind: str | None = None, status: str | None = None,
                    page: Page = Depends(pagination),
                    principal: Principal = Depends(current_user),
                    session: AsyncSession = Depends(get_session)) -> list[JobOut]:
    principal.require("viewer")
    stmt = sa.select(Job)
    if kind:
        stmt = stmt.where(Job.kind == kind)
    if status:
        stmt = stmt.where(Job.status == status)
    rows = (
        await session.execute(
            stmt.order_by(Job.created_at.desc()).limit(page.limit).offset(page.offset)
        )
    ).scalars().all()
    return [JobOut.model_validate(r) for r in rows]


@router.post("", response_model=JobOut, status_code=202)
async def create_job(body: AsyncJobIn, request: Request,
                     principal: Principal = Depends(current_user),
                     session: AsyncSession = Depends(get_session)) -> JobOut:
    """Submit -> job id -> poll or webhook. Use this for video and bulk folders."""
    principal.require("developer")
    model = await get_model_or_404(session, body.model)

    sources = list(body.source_keys or []) + list(body.source_urls or [])
    if body.kind == "video":
        if not body.video_key:
            raise ApiError("invalid_request",
                           "a video job needs video_key pointing at an uploaded video")
        total = 0
    else:
        if not sources:
            raise ApiError("invalid_request",
                           "give source_keys (already in storage) or source_urls to process")
        total = len(sources)

    job = Job(kind=body.kind, status="queued", total=total,
              webhook_url=body.webhook_url, created_by=principal.id,
              params={**body.model_dump(), "model_id": model.id, "model_slug": model.slug})
    session.add(job)
    await session.flush()
    await audit.record(session, action="job.create", actor_id=principal.id,
                       actor_label=principal.label, target_type="job", target_id=job.id,
                       target_label=f"{body.kind} on {model.slug}",
                       payload={"kind": body.kind, "count": total},
                       client_ip=request.client.host if request.client else None)
    await session.commit()
    await submit(session, job, "run_inference_job")
    return JobOut.model_validate(job)


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: str, principal: Principal = Depends(current_user),
                  session: AsyncSession = Depends(get_session)) -> JobOut:
    job = await session.get(Job, job_id)
    if job is None:
        raise ApiError("job_not_found", "no such job")
    principal.require("viewer")
    return JobOut.model_validate(job)


@router.get("/{job_id}/result")
async def job_result(job_id: str, principal: Principal = Depends(current_user),
                     session: AsyncSession = Depends(get_session)) -> dict:
    """Large results live in object storage; this hands back a signed link."""
    job = await session.get(Job, job_id)
    if job is None:
        raise ApiError("job_not_found", "no such job")
    principal.require("viewer")
    if job.status != "succeeded":
        raise ApiError("conflict", f"this job is {job.status}")
    key = (job.result or {}).get("artifact_key")
    if not key:
        return job.result or {}
    return {
        **job.result,
        "url": await storage.presign_get(settings.s3_bucket_reports, key, 3600, public=True),
    }


@router.post("/{job_id}/cancel", response_model=JobOut)
async def cancel_job(job_id: str, request: Request,
                     principal: Principal = Depends(current_user),
                     session: AsyncSession = Depends(get_session)) -> JobOut:
    job = await session.get(Job, job_id)
    if job is None:
        raise ApiError("job_not_found", "no such job")
    principal.require("developer")
    if job.status in ("succeeded", "failed", "cancelled"):
        raise ApiError("conflict", f"this job already finished as '{job.status}'")
    job.status = "cancelled"
    await audit.record(session, action="job.cancel", actor_id=principal.id,
                       actor_label=principal.label, target_type="job",
                       target_id=job.id, client_ip=request.client.host if request.client else None)
    await session.commit()
    return JobOut.model_validate(job)


@router.post("/exports/flagged", response_model=JobOut, status_code=202)
async def export_flagged(
    request: Request,
    model_id: str | None = None,
    since_hours: int = Query(24 * 90, ge=1, le=24 * 400),
    fmt: str = Query("coco", pattern="^(coco|yolo|jsonl)$"),
    principal: Principal = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> JobOut:
    """R5.10 - package flagged predictions (images + labels) as a retraining dataset."""
    principal.require("developer")
    job = Job(kind="export_flagged", status="queued",
              created_by=principal.id,
              params={"model_id": model_id, "since_hours": since_hours, "fmt": fmt})
    session.add(job)
    await session.flush()
    await audit.record(session, action="capture.export", actor_id=principal.id,
                       actor_label=principal.label, target_type="job",
                       target_id=job.id, payload={"fmt": fmt, "since_hours": since_hours},
                       client_ip=request.client.host if request.client else None)
    await session.commit()
    await submit(session, job, "export_flagged_dataset")
    return JobOut.model_validate(job)


@router.post("/captures/purge", response_model=JobOut, status_code=202)
async def purge_captures(
    request: Request,
    before_hours: int = Query(0, ge=0, description="0 purges everything past its retention date"),
    model_id: str | None = None,
    principal: Principal = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> JobOut:
    """R7.6 - hard delete captured images and their log payloads."""
    principal.require("admin")
    job = Job(kind="purge_captures", status="queued", created_by=principal.id,
              params={"before_hours": before_hours, "model_id": model_id,
                      "requested_by": principal.label})
    session.add(job)
    await session.flush()
    await audit.record(session, action="capture.purge", actor_id=principal.id,
                       actor_label=principal.label, target_type="job",
                       target_id=job.id,
                       payload={"before_hours": before_hours, "model_id": model_id},
                       client_ip=request.client.host if request.client else None)
    await session.commit()
    await submit(session, job, "purge_captures")
    return JobOut.model_validate(job)
