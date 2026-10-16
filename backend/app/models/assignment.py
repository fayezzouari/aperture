"""The `Assignment` table."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, new_id


class Assignment(Base, TimestampMixin):
    """Which runtime should hold which version - one row per replica."""

    __tablename__ = "assignments"
    __table_args__ = (
        sa.UniqueConstraint("runtime_id", "version_id", name="uq_assign_runtime_version"),
    )

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_id)
    deployment_id: Mapped[str] = mapped_column(
        sa.ForeignKey("deployments.id", ondelete="CASCADE"), index=True
    )
    runtime_id: Mapped[str] = mapped_column(sa.String(64), index=True)
    version_id: Mapped[str] = mapped_column(sa.String(36), index=True)
    state: Mapped[str] = mapped_column(sa.String(24), default="pending")
    # pending -> loading -> warming -> ready | failed ; draining
    detail: Mapped[str] = mapped_column(sa.Text, default="")
    ready_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
