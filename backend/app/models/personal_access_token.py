"""The `PersonalAccessToken` table."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, new_id


class PersonalAccessToken(Base, TimestampMixin):
    """R8.1 - a long-lived credential that *is* the user.

    Issued for agents and scripts that outlive a browser session. It carries no
    scopes and no rate limit: it inherits the owner's role, and the
    owner can revoke it at any time. Like an API key it is stored only as a hash.
    """

    __tablename__ = "personal_access_tokens"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(sa.String(160), nullable=False)
    prefix: Mapped[str] = mapped_column(sa.String(24), unique=True, index=True)
    token_hash: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
