"""The `InstanceSettings` table."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class InstanceSettings(Base):
    """Instance-wide overrides of the env defaults; always the single row with id 1.

    Empty columns fall back to AP_CAPTURE_SAMPLE_RATE and AP_CAPTURE_RETENTION_DAYS.
    """

    __tablename__ = "instance_settings"

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, default=1)
    capture_sample_rate: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    capture_retention_days: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(),
        nullable=False,
    )
