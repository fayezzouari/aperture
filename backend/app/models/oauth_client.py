"""The `OAuthClient` table."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import JSON_T, Base, TimestampMixin


class OAuthClient(Base, TimestampMixin):
    """An MCP client that registered itself (RFC 7591) to ask people for access.

    Registration is open, as MCP clients expect, and grants nothing: a client
    only ever gets a token when a signed-in person approves it on the consent
    page, and then only to one of the redirect URIs it registered here. Clients
    are public (they hold no secret); PKCE binds the code to whoever asked.
    """

    __tablename__ = "oauth_clients"

    id: Mapped[str] = mapped_column(sa.String(64), primary_key=True)  # the client_id
    client_name: Mapped[str] = mapped_column(sa.String(160), nullable=False)
    redirect_uris: Mapped[list] = mapped_column(JSON_T, default=list)
    client_uri: Mapped[str | None] = mapped_column(sa.String(512))
