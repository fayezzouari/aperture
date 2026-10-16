"""The `AlertEvent` table."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, new_id


class AlertEvent(Base):
    __tablename__ = "alert_events"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_id)
    rule_id: Mapped[str] = mapped_column(sa.ForeignKey("alert_rules.id", ondelete="CASCADE"),
                                        index=True)
    project_id: Mapped[str] = mapped_column(sa.String(36), index=True)
    value: Mapped[float] = mapped_column(sa.Float, default=0.0)
    message: Mapped[str] = mapped_column(sa.Text, default="")
    delivered: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    delivery_error: Mapped[str | None] = mapped_column(sa.Text)
    resolved_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True
    )
