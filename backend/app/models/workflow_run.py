"""The `WorkflowRun` table."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import JSON_T, Base, new_id


class WorkflowRun(Base):
    """One execution, with per-node timings and status."""

    __tablename__ = "workflow_runs"
    __table_args__ = (sa.Index("ix_wfrun_workflow_created", "workflow_id", "created_at"),)

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_id)
    workflow_id: Mapped[str] = mapped_column(
        sa.ForeignKey("workflows.id", ondelete="CASCADE"), index=True
    )
    request_id: Mapped[str] = mapped_column(sa.String(64), index=True)
    trace_id: Mapped[str | None] = mapped_column(sa.String(64))
    revision: Mapped[int] = mapped_column(sa.Integer, default=0)
    status: Mapped[str] = mapped_column(sa.String(16), default="ok")  # ok|error|partial
    error_code: Mapped[str | None] = mapped_column(sa.String(64))
    error_message: Mapped[str | None] = mapped_column(sa.Text)
    failed_node: Mapped[str | None] = mapped_column(sa.String(64))
    caller_type: Mapped[str] = mapped_column(sa.String(16), default="api_key")
    caller_id: Mapped[str | None] = mapped_column(sa.String(36))
    caller_label: Mapped[str] = mapped_column(sa.String(160), default="")
    image_count: Mapped[int] = mapped_column(sa.Integer, default=0)
    model_calls: Mapped[int] = mapped_column(sa.Integer, default=0)
    total_ms: Mapped[float] = mapped_column(sa.Float, default=0.0, index=True)
    node_stats: Mapped[list] = mapped_column(JSON_T, default=list)
    output: Mapped[dict] = mapped_column(JSON_T, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True
    )
