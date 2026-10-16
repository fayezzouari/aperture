"""The `ApiKey` table."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import JSON_T, Base, TimestampMixin, new_id


class ApiKey(Base, TimestampMixin):
    """R7.1 - scoped, revocable, never stored in the clear."""

    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        sa.ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(sa.String(160), nullable=False)
    prefix: Mapped[str] = mapped_column(sa.String(24), unique=True, index=True)
    key_hash: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    scopes: Mapped[list] = mapped_column(JSON_T, default=list)  # ["*"] or model ids
    rate_limit_rpm: Mapped[int] = mapped_column(sa.Integer, default=600)
    monthly_quota: Mapped[int] = mapped_column(sa.Integer, default=0)
    created_by: Mapped[str | None] = mapped_column(sa.String(36))
    last_used_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
