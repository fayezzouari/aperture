"""The `Runtime` table."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import JSON_T, Base, TimestampMixin


class Runtime(Base, TimestampMixin):
    """A serving process that heartbeats in and is handed assignments."""

    __tablename__ = "runtimes"

    id: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    url: Mapped[str] = mapped_column(sa.String(320), nullable=False)
    hostname: Mapped[str] = mapped_column(sa.String(160), default="")
    device: Mapped[str] = mapped_column(sa.String(24), default="cpu")
    gpu_name: Mapped[str] = mapped_column(sa.String(160), default="")
    total_memory_mb: Mapped[int] = mapped_column(sa.Integer, default=0)
    free_memory_mb: Mapped[int] = mapped_column(sa.Integer, default=0)
    slots: Mapped[int] = mapped_column(sa.Integer, default=4)
    build: Mapped[str] = mapped_column(sa.String(64), default="")
    status: Mapped[str] = mapped_column(sa.String(24), default="ready", index=True)
    loaded: Mapped[list] = mapped_column(JSON_T, default=list)   # [{version_id, state, ...}]
    metrics: Mapped[dict] = mapped_column(JSON_T, default=dict)
    last_heartbeat: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), index=True
    )
