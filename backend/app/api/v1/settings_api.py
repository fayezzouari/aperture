"""Instance settings an admin changes at runtime: capture sampling and retention."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit
from app.db import get_session
from app.deps import Principal, current_user
from app.models import InstanceSettings
from app.schemas import InstanceSettingsOut, InstanceSettingsPatch
from app.services import capture as capture_service

router = APIRouter(prefix="/settings", tags=["settings"])


def _out(row: InstanceSettings | None) -> InstanceSettingsOut:
    rate = row.capture_sample_rate if row else None
    days = row.capture_retention_days if row else None
    return InstanceSettingsOut(
        capture_sample_rate=rate,
        capture_retention_days=days,
        effective_sample_rate=capture_service.effective_rate(rate),
        effective_retention_days=capture_service.retention_days(days),
    )


@router.get("", response_model=InstanceSettingsOut)
async def get_settings(principal: Principal = Depends(current_user),
                       session: AsyncSession = Depends(get_session)) -> InstanceSettingsOut:
    principal.require("viewer")
    return _out(await session.get(InstanceSettings, 1))


@router.patch("", response_model=InstanceSettingsOut)
async def update_settings(body: InstanceSettingsPatch, request: Request,
                          principal: Principal = Depends(current_user),
                          session: AsyncSession = Depends(get_session)) -> InstanceSettingsOut:
    """Fields sent as null clear the override and fall back to the AP_CAPTURE_* default."""
    principal.require("admin")
    row = await session.get(InstanceSettings, 1)
    if row is None:
        row = InstanceSettings(id=1)
        session.add(row)
    changes = body.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(row, key, value)
    await audit.record(session, action="settings.update", actor_id=principal.id,
                       actor_label=principal.label, target_type="settings",
                       target_label="instance", payload=changes,
                       client_ip=request.client.host if request.client else None)
    await session.commit()
    await session.refresh(row)
    capture_service.forget_policy()
    return _out(row)
