"""The `AuditLog` table."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import JSON_T, Base, new_id


class AuditLog(Base):
    """R7.4 - append-only record of every mutating action."""

    __tablename__ = "audit_logs"
    __table_args__ = (sa.Index("ix_audit_project_created", "project_id", "created_at"),)

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_id)
    project_id: Mapped[str | None] = mapped_column(sa.String(36), index=True)
    actor_type: Mapped[str] = mapped_column(sa.String(16), default="user")
    actor_id: Mapped[str | None] = mapped_column(sa.String(36))
    actor_label: Mapped[str] = mapped_column(sa.String(200), default="")
    action: Mapped[str] = mapped_column(sa.String(64), index=True)
    target_type: Mapped[str] = mapped_column(sa.String(48), default="")
    target_id: Mapped[str | None] = mapped_column(sa.String(96))
    target_label: Mapped[str] = mapped_column(sa.String(200), default="")
    payload: Mapped[dict] = mapped_column(JSON_T, default=dict)
    client_ip: Mapped[str | None] = mapped_column(sa.String(64))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True
    )
