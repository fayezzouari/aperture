"""The `Model` table."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import JSON_T, Base, TimestampMixin, new_id

if TYPE_CHECKING:
    from app.models.model_version import ModelVersion
    from app.models.tag import Tag


class Model(Base, TimestampMixin):
    __tablename__ = "models"
    __table_args__ = (
        sa.UniqueConstraint("workspace_id", "slug", name="uq_model_workspace_slug"),
    )

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        sa.ForeignKey("workspaces.id", ondelete="CASCADE"), index=True, nullable=False
    )
    slug: Mapped[str] = mapped_column(sa.String(96), nullable=False, index=True)
    name: Mapped[str] = mapped_column(sa.String(160), nullable=False)
    task: Mapped[str] = mapped_column(sa.String(32), nullable=False, index=True)
    owner: Mapped[str] = mapped_column(sa.String(160), default="")
    framework: Mapped[str] = mapped_column(sa.String(32), default="onnx")
    description: Mapped[str] = mapped_column(sa.Text, default="")
    labels: Mapped[list] = mapped_column(JSON_T, default=list)
    archived_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    versions: Mapped[list[ModelVersion]] = relationship(
        back_populates="model", order_by="desc(ModelVersion.version)",
        cascade="all, delete-orphan", passive_deletes=True,
    )
    tags: Mapped[list[Tag]] = relationship(
        back_populates="model", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def storage_prefix(self) -> str:
        from app import storage

        return storage.model_prefix(self.workspace_id, self.slug)
