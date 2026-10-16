"""Audit trail rows."""

from __future__ import annotations

from datetime import datetime

from app.schemas.common import ORMModel


class AuditOut(ORMModel):
    id: str
    project_id: str | None
    actor_type: str
    actor_id: str | None
    actor_label: str
    action: str
    target_type: str
    target_id: str | None
    target_label: str
    payload: dict
    client_ip: str | None
    created_at: datetime
