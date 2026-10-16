"""The `UploadSession` table."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import JSON_T, Base, TimestampMixin, new_id


class UploadSession(Base, TimestampMixin):
    """R1.1 - resumable multipart upload for multi-GB weights."""

    __tablename__ = "upload_sessions"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(sa.String(36), index=True)
    model_id: Mapped[str | None] = mapped_column(sa.String(36), index=True)
    filename: Mapped[str] = mapped_column(sa.String(320))
    total_bytes: Mapped[int] = mapped_column(sa.BigInteger, default=0)
    received_bytes: Mapped[int] = mapped_column(sa.BigInteger, default=0)
    chunk_bytes: Mapped[int] = mapped_column(sa.Integer, default=32 * 1024 * 1024)
    key: Mapped[str] = mapped_column(sa.String(512))
    multipart_id: Mapped[str | None] = mapped_column(sa.String(320))
    parts: Mapped[list] = mapped_column(JSON_T, default=list)  # [{part, etag, size, sha256}]
    sha256_expected: Mapped[str | None] = mapped_column(sa.String(64))
    status: Mapped[str] = mapped_column(sa.String(24), default="open")
    created_by: Mapped[str | None] = mapped_column(sa.String(36))
    expires_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
