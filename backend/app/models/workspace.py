"""The `Workspace` table."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, new_id


class Workspace(Base, TimestampMixin):
    """R7.3 - a context that owns its models and workflows; members decide who sees it.

    The slug is fixed once created: it prefixes the object-store keys of every
    model in the workspace, so renaming changes only the display name.
    """

    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_id)
    slug: Mapped[str] = mapped_column(sa.String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(sa.String(160), nullable=False)
    description: Mapped[str] = mapped_column(sa.Text, default="")
