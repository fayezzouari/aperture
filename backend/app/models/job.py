"""The `Job` table."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import JSON_T, Base, TimestampMixin, new_id


class Job(Base, TimestampMixin):
    """R3.8 / R1.10 - async work: bulk + video inference, conversion, exports, reports."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(sa.String(48), index=True)
    status: Mapped[str] = mapped_column(sa.String(24), default="queued", index=True)
    # queued -> running -> succeeded | failed | cancelled
    params: Mapped[dict] = mapped_column(JSON_T, default=dict)
    progress: Mapped[int] = mapped_column(sa.Integer, default=0)
    total: Mapped[int] = mapped_column(sa.Integer, default=0)
    result: Mapped[dict] = mapped_column(JSON_T, default=dict)
    error: Mapped[str | None] = mapped_column(sa.Text)
    webhook_url: Mapped[str | None] = mapped_column(sa.String(512))
    created_by: Mapped[str | None] = mapped_column(sa.String(36))
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
