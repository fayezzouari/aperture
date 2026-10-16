"""The `DriftWindow` table."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import JSON_T, Base, new_id


class DriftWindow(Base):
    """R5.9 - hourly rollup of confidence, class mix and input statistics."""

    __tablename__ = "drift_windows"
    __table_args__ = (
        sa.UniqueConstraint("version_id", "window_start", name="uq_drift_version_window"),
    )

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_id)
    model_id: Mapped[str] = mapped_column(sa.String(36), index=True)
    version_id: Mapped[str] = mapped_column(sa.String(36), index=True)
    window_start: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), index=True)
    window_end: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    request_count: Mapped[int] = mapped_column(sa.Integer, default=0)
    stats: Mapped[dict] = mapped_column(JSON_T, default=dict)
    is_baseline: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
