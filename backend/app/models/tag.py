"""The `Tag` table."""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, new_id

if TYPE_CHECKING:
    from app.models.model import Model


class Tag(Base, TimestampMixin):
    """R2.2/R2.3 - a movable pointer. Clients call the tag, we resolve it per request."""

    __tablename__ = "tags"
    __table_args__ = (sa.UniqueConstraint("model_id", "name", name="uq_tag_model_name"),)

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_id)
    model_id: Mapped[str] = mapped_column(
        sa.ForeignKey("models.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    # cascade so deleting a model cannot leave a tag pointing at a deleted version
    version_id: Mapped[str] = mapped_column(
        sa.ForeignKey("model_versions.id", ondelete="CASCADE"), nullable=False
    )
    previous_version_id: Mapped[str | None] = mapped_column(sa.String(36))
    updated_by: Mapped[str | None] = mapped_column(sa.String(36))
    locked: Mapped[bool] = mapped_column(sa.Boolean, default=False)

    model: Mapped[Model] = relationship(back_populates="tags")
