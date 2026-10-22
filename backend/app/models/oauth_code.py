"""The `OAuthCode` table."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class OAuthCode(Base, TimestampMixin):
    """An authorization code: what the consent page hands back to the client.

    Lives for minutes, is stored only as a hash, and is spent on first use. It
    remembers everything the token exchange has to match - the client, the exact
    redirect URI and the PKCE challenge - so a stolen code is useless without
    the verifier only the client that asked for it holds.
    """

    __tablename__ = "oauth_codes"

    code_hash: Mapped[str] = mapped_column(sa.String(128), primary_key=True)
    client_id: Mapped[str] = mapped_column(
        sa.ForeignKey("oauth_clients.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    redirect_uri: Mapped[str] = mapped_column(sa.String(2048), nullable=False)
    code_challenge: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    resource: Mapped[str | None] = mapped_column(sa.String(2048))
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
