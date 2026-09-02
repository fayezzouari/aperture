"""R7.4 - reading the audit trail."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import ACTIONS
from app.db import get_session
from app.deps import Page, Principal, current_user, pagination, project_dep
from app.models import AuditLog, Project
from app.schemas import AuditOut

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/actions", response_model=list[str])
async def known_actions() -> list[str]:
    return sorted(ACTIONS)


@router.get("", response_model=list[AuditOut])
async def list_audit(
    action: str | None = None,
    actor: str | None = None,
    target_type: str | None = None,
    since_hours: int = Query(24 * 30, ge=1, le=24 * 400),
    page: Page = Depends(pagination),
    project: Project = Depends(project_dep),
    principal: Principal = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[AuditOut]:
    principal.require(project.id, "viewer")
    since = datetime.now(UTC) - timedelta(hours=since_hours)
    stmt = sa.select(AuditLog).where(AuditLog.project_id == project.id,
                                     AuditLog.created_at >= since)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if actor:
        stmt = stmt.where(sa.func.lower(AuditLog.actor_label).like(f"%{actor.lower()}%"))
    if target_type:
        stmt = stmt.where(AuditLog.target_type == target_type)
    rows = (
        await session.execute(
            stmt.order_by(AuditLog.created_at.desc()).limit(page.limit).offset(page.offset)
        )
    ).scalars().all()
    return [AuditOut.model_validate(r) for r in rows]
