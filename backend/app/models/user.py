"""The `User` table."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, new_id


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(sa.String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(sa.String(160), default="")
    password_hash: Mapped[str | None] = mapped_column(sa.String(255))
    provider: Mapped[str] = mapped_column(sa.String(32), default="local")  # local|oidc|ldap
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    # R7.2 - the instance role: admin runs the instance and every workspace in it,
    # developer can also create workspaces; inside a workspace the member role applies
    role: Mapped[str] = mapped_column(sa.String(16), default="viewer", nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    # the workspace the UI opens on sign-in: the one this user switched to last
    last_workspace_id: Mapped[str | None] = mapped_column(
        sa.ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True
    )
