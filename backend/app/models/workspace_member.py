"""The `WorkspaceMember` table."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, new_id


class WorkspaceMember(Base, TimestampMixin):
    """R7.2 - a user's role inside one workspace: viewer | developer | admin.

    Instance admins need no row: they hold the admin role in every workspace.
    """

    __tablename__ = "workspace_members"
    __table_args__ = (
        sa.UniqueConstraint("user_id", "workspace_id", name="uq_member_user_workspace"),
    )

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[str] = mapped_column(
        sa.ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(sa.String(16), default="viewer", nullable=False)
