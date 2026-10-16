"""The `ReportSchedule` table."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import JSON_T, Base, TimestampMixin, new_id


class ReportSchedule(Base, TimestampMixin):
    """R5.7 - a saved report definition plus an optional cron for delivery."""

    __tablename__ = "report_schedules"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(sa.String(36), index=True)
    name: Mapped[str] = mapped_column(sa.String(160))
    kind: Mapped[str] = mapped_column(sa.String(32), default="usage")
    params: Mapped[dict] = mapped_column(JSON_T, default=dict)
    fmt: Mapped[str] = mapped_column(sa.String(8), default="csv")  # csv|pdf
    cron: Mapped[str | None] = mapped_column(sa.String(64))  # m h dom mon dow
    channel: Mapped[str] = mapped_column(sa.String(16), default="none")
    destination: Mapped[str] = mapped_column(sa.String(512), default="")
    enabled: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_artifact_key: Mapped[str | None] = mapped_column(sa.String(512))
    created_by: Mapped[str | None] = mapped_column(sa.String(36))
