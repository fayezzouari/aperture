"""The `Deployment` table."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, new_id


class Deployment(Base, TimestampMixin):
    """Desired serving state for one version. The reconciler turns it into assignments."""

    __tablename__ = "deployments"
    __table_args__ = (sa.UniqueConstraint("version_id", "mode", name="uq_deploy_version_mode"),)

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_id)
    model_id: Mapped[str] = mapped_column(
        sa.ForeignKey("models.id", ondelete="CASCADE"), index=True
    )
    version_id: Mapped[str] = mapped_column(
        sa.ForeignKey("model_versions.id", ondelete="CASCADE"), index=True
    )
    mode: Mapped[str] = mapped_column(sa.String(16), default="primary")  # primary|canary|shadow
    traffic_weight: Mapped[int] = mapped_column(sa.Integer, default=100)  # R2.7
    replicas: Mapped[int] = mapped_column(sa.Integer, default=1)
    device: Mapped[str] = mapped_column(sa.String(24), default="auto")  # auto|cpu|cuda:0
    max_batch_size: Mapped[int] = mapped_column(sa.Integer, default=8)   # R4.2
    max_wait_ms: Mapped[int] = mapped_column(sa.Integer, default=8)
    max_queue_depth: Mapped[int] = mapped_column(sa.Integer, default=64)  # R4.3
    request_timeout_ms: Mapped[int] = mapped_column(sa.Integer, default=30_000)
    min_replicas: Mapped[int] = mapped_column(sa.Integer, default=1)      # R4.8
    max_replicas: Mapped[int] = mapped_column(sa.Integer, default=1)
    autoscale: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    target_queue_depth: Mapped[int] = mapped_column(sa.Integer, default=8)
    status: Mapped[str] = mapped_column(sa.String(24), default="pending", index=True)
    # pending -> warming -> live -> draining -> stopped | failed
    status_detail: Mapped[str] = mapped_column(sa.Text, default="")
    stopped_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
