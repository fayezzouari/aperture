"""The `Membership` table."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, new_id


class Membership(Base, TimestampMixin):
    """R7.2 - RBAC is per project: viewer | developer | admin."""

    __tablename__ = "memberships"
    __table_args__ = (sa.UniqueConstraint("user_id", "project_id", name="uq_member_user_project"),)

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(
        sa.ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(sa.String(16), default="viewer")
