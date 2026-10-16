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
    is_superadmin: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
