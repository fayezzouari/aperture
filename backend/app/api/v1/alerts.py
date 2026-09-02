"""R5.8 - alert rules."""

from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit
from app.db import get_session
from app.deps import Principal, current_user, project_dep
from app.errors import ApiError
from app.models import AlertEvent, AlertRule, Project
from app.schemas import AlertRuleIn, AlertRuleOut
from app.services import alerts as alert_service

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=list[AlertRuleOut])
async def list_rules(project: Project = Depends(project_dep),
                     principal: Principal = Depends(current_user),
                     session: AsyncSession = Depends(get_session)) -> list[AlertRuleOut]:
    principal.require(project.id, "viewer")
    rows = (
        await session.execute(
            sa.select(AlertRule).where(AlertRule.project_id == project.id)
            .order_by(AlertRule.created_at.desc())
        )
    ).scalars().all()
    return [AlertRuleOut.model_validate(r) for r in rows]


@router.post("", response_model=AlertRuleOut, status_code=201)
async def create_rule(body: AlertRuleIn, request: Request, project: Project = Depends(project_dep),
                      principal: Principal = Depends(current_user),
                      session: AsyncSession = Depends(get_session)) -> AlertRuleOut:
    principal.require(project.id, "developer")
    if not body.destination:
        raise ApiError("invalid_request", f"a {body.channel} destination is required")
    rule = AlertRule(project_id=project.id, **body.model_dump())
    session.add(rule)
    await session.flush()
    await audit.record(session, action="alert.create", project_id=project.id,
                       actor_id=principal.id, actor_label=principal.label, target_type="alert",
                       target_id=rule.id, target_label=body.name, payload=body.model_dump(),
                       client_ip=request.client.host if request.client else None)
    await session.commit()
    return AlertRuleOut.model_validate(rule)


@router.patch("/{rule_id}", response_model=AlertRuleOut)
async def patch_rule(rule_id: str, body: AlertRuleIn, request: Request,
                     principal: Principal = Depends(current_user),
                     session: AsyncSession = Depends(get_session)) -> AlertRuleOut:
    rule = await session.get(AlertRule, rule_id)
    if rule is None:
        raise ApiError("not_found", "no such alert rule")
    principal.require(rule.project_id, "developer")
    for key, value in body.model_dump().items():
        setattr(rule, key, value)
    await audit.record(session, action="alert.update", project_id=rule.project_id,
                       actor_id=principal.id, actor_label=principal.label, target_type="alert",
                       target_id=rule.id, target_label=rule.name, payload=body.model_dump(),
                       client_ip=request.client.host if request.client else None)
    await session.commit()
    return AlertRuleOut.model_validate(rule)


@router.delete("/{rule_id}", status_code=204)
async def delete_rule(rule_id: str, request: Request,
                      principal: Principal = Depends(current_user),
                      session: AsyncSession = Depends(get_session)) -> None:
    rule = await session.get(AlertRule, rule_id)
    if rule is None:
        raise ApiError("not_found", "no such alert rule")
    principal.require(rule.project_id, "developer")
    await audit.record(session, action="alert.delete", project_id=rule.project_id,
                       actor_id=principal.id, actor_label=principal.label, target_type="alert",
                       target_id=rule.id, target_label=rule.name,
                       client_ip=request.client.host if request.client else None)
    await session.delete(rule)
    await session.commit()


@router.post("/{rule_id}/test")
async def test_rule(rule_id: str, principal: Principal = Depends(current_user),
                    session: AsyncSession = Depends(get_session)) -> dict:
    """Send one message through the configured channel so you know it lands."""
    rule = await session.get(AlertRule, rule_id)
    if rule is None:
        raise ApiError("not_found", "no such alert rule")
    principal.require(rule.project_id, "developer")
    delivered, error = await alert_service.deliver(
        rule, f"Test message for '{rule.name}' - delivery is working.", value=0.0
    )
    return {"delivered": delivered, "error": error, "channel": rule.channel}


@router.get("/events")
async def list_events(limit: int = 50, project: Project = Depends(project_dep),
                      principal: Principal = Depends(current_user),
                      session: AsyncSession = Depends(get_session)) -> list[dict]:
    principal.require(project.id, "viewer")
    rows = (
        await session.execute(
            sa.select(AlertEvent, AlertRule.name)
            .join(AlertRule, AlertRule.id == AlertEvent.rule_id)
            .where(AlertEvent.project_id == project.id)
            .order_by(AlertEvent.created_at.desc()).limit(min(limit, 500))
        )
    ).all()
    return [
        {"id": e.id, "rule_id": e.rule_id, "rule": name, "value": e.value, "message": e.message,
         "delivered": e.delivered, "delivery_error": e.delivery_error,
         "resolved_at": e.resolved_at, "created_at": e.created_at}
        for e, name in rows
    ]
