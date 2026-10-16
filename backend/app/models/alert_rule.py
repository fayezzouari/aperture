"""The `AlertRule` table."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, new_id


class AlertRule(Base, TimestampMixin):
    """R5.8 - evaluated every minute by the scheduled worker."""

    __tablename__ = "alert_rules"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(sa.String(160))
    metric: Mapped[str] = mapped_column(sa.String(32))
    # error_rate | p99_latency_ms | p95_latency_ms | no_traffic | replica_down | confidence_drop
    model_id: Mapped[str | None] = mapped_column(sa.String(36))
    version_id: Mapped[str | None] = mapped_column(sa.String(36))
    comparator: Mapped[str] = mapped_column(sa.String(4), default=">")
    threshold: Mapped[float] = mapped_column(sa.Float, default=0.0)
    window_minutes: Mapped[int] = mapped_column(sa.Integer, default=5)
    min_samples: Mapped[int] = mapped_column(sa.Integer, default=20)
    channel: Mapped[str] = mapped_column(sa.String(16), default="webhook")  # slack|email|webhook
    destination: Mapped[str] = mapped_column(sa.String(512), default="")
    enabled: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    state: Mapped[str] = mapped_column(sa.String(16), default="ok")  # ok|firing
    last_value: Mapped[float | None] = mapped_column(sa.Float)
    last_fired_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_checked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
