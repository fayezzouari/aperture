"""The `Project` table."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, new_id

if TYPE_CHECKING:
    from app.models.model import Model


class Project(Base, TimestampMixin):
    """R7.3 - the isolation unit for models, keys, logs and quotas."""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_id)
    slug: Mapped[str] = mapped_column(sa.String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(sa.String(160), nullable=False)
    description: Mapped[str] = mapped_column(sa.Text, default="")
    capture_sample_rate: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    capture_retention_days: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    # passive_deletes lets Postgres' ON DELETE CASCADE do the work; without it
    # SQLAlchemy tries to NULL the child FK first, which the schema forbids.
    models: Mapped[list[Model]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )
