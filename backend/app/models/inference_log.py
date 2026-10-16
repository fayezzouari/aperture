"""The `InferenceLog` table."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import JSON_T, Base, new_id


class InferenceLog(Base):
    """R5.1 - one row per inference request. Written asynchronously off the hot path."""

    __tablename__ = "inference_logs"
    __table_args__ = (
        sa.Index("ix_inflog_model_created", "model_id", "created_at"),
        sa.Index("ix_inflog_status_created", "status", "created_at"),
        sa.Index("ix_inflog_version_created", "version_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_id)
    request_id: Mapped[str] = mapped_column(sa.String(64), index=True)
    trace_id: Mapped[str | None] = mapped_column(sa.String(64), index=True)
    span_id: Mapped[str | None] = mapped_column(sa.String(32))

    model_id: Mapped[str | None] = mapped_column(sa.String(36))
    version_id: Mapped[str | None] = mapped_column(sa.String(36))
    model_slug: Mapped[str] = mapped_column(sa.String(96), default="")
    version_number: Mapped[int | None] = mapped_column(sa.Integer)
    tag: Mapped[str | None] = mapped_column(sa.String(64))
    route: Mapped[str] = mapped_column(sa.String(24), default="primary")  # primary|canary|shadow

    caller_type: Mapped[str] = mapped_column(sa.String(16), default="api_key")
    caller_id: Mapped[str | None] = mapped_column(sa.String(36), index=True)
    caller_label: Mapped[str] = mapped_column(sa.String(160), default="")
    client_ip: Mapped[str | None] = mapped_column(sa.String(64))

    status: Mapped[str] = mapped_column(sa.String(16), default="ok")  # ok|error|timeout|rejected
    http_status: Mapped[int] = mapped_column(sa.Integer, default=200)
    error_code: Mapped[str | None] = mapped_column(sa.String(64))
    error_message: Mapped[str | None] = mapped_column(sa.Text)

    batch_size: Mapped[int] = mapped_column(sa.Integer, default=1)
    queue_ms: Mapped[float] = mapped_column(sa.Float, default=0.0)
    preprocess_ms: Mapped[float] = mapped_column(sa.Float, default=0.0)
    inference_ms: Mapped[float] = mapped_column(sa.Float, default=0.0)
    postprocess_ms: Mapped[float] = mapped_column(sa.Float, default=0.0)
    gateway_ms: Mapped[float] = mapped_column(sa.Float, default=0.0)
    total_ms: Mapped[float] = mapped_column(sa.Float, default=0.0, index=True)

    runtime_id: Mapped[str | None] = mapped_column(sa.String(64))
    input_ref: Mapped[dict] = mapped_column(JSON_T, default=dict)   # {source, names, bytes, sha256}
    input_stats: Mapped[dict] = mapped_column(JSON_T, default=dict)  # R5.9
    output: Mapped[dict] = mapped_column(JSON_T, default=dict)       # truncated predictions
    confidence: Mapped[dict] = mapped_column(JSON_T, default=dict)
    overrides: Mapped[dict] = mapped_column(JSON_T, default=dict)
    capture: Mapped[dict] = mapped_column(JSON_T, default=dict)      # {images: [keys], overlay: key}
    detection_count: Mapped[int] = mapped_column(sa.Integer, default=0)

    flagged: Mapped[bool] = mapped_column(sa.Boolean, default=False, index=True)
    flag_label: Mapped[str | None] = mapped_column(sa.String(64))
    flag_note: Mapped[str | None] = mapped_column(sa.Text)
    flagged_by: Mapped[str | None] = mapped_column(sa.String(36))

    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True
    )
    purge_after: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), index=True)
