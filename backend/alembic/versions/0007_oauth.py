"""OAuth for MCP clients: registered clients, authorization codes, and grants
stored as personal access tokens with a refresh token.

Guarded like 0002 and 0006: on a running install the API's startup has already
created the new tables (create_all), but it never adds columns to an existing
one, so the token columns are checked one by one.

Revision ID: 0007_oauth
Revises: 0006_workspace_keys
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.models.base import JSON_T

revision = "0007_oauth"
down_revision = "0006_workspace_keys"
branch_labels = None
depends_on = None

TOKENS = "personal_access_tokens"


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
    ]


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("oauth_clients"):
        op.create_table(
            "oauth_clients",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("client_name", sa.String(160), nullable=False),
            sa.Column("redirect_uris", JSON_T),
            sa.Column("client_uri", sa.String(512)),
            *_timestamps(),
        )
        op.create_index("ix_oauth_clients_created_at", "oauth_clients", ["created_at"])

    if not inspector.has_table("oauth_codes"):
        op.create_table(
            "oauth_codes",
            sa.Column("code_hash", sa.String(128), primary_key=True),
            sa.Column("client_id", sa.String(64),
                      sa.ForeignKey("oauth_clients.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("redirect_uri", sa.String(2048), nullable=False),
            sa.Column("code_challenge", sa.String(128), nullable=False),
            sa.Column("resource", sa.String(2048)),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True)),
            *_timestamps(),
        )
        op.create_index("ix_oauth_codes_client_id", "oauth_codes", ["client_id"])
        op.create_index("ix_oauth_codes_user_id", "oauth_codes", ["user_id"])
        op.create_index("ix_oauth_codes_created_at", "oauth_codes", ["created_at"])

    columns = {c["name"] for c in inspector.get_columns(TOKENS)}
    if "oauth_client_id" not in columns:
        op.add_column(TOKENS, sa.Column("oauth_client_id", sa.String(64), nullable=True))
        op.create_foreign_key(f"{TOKENS}_oauth_client_id_fkey", TOKENS, "oauth_clients",
                              ["oauth_client_id"], ["id"], ondelete="CASCADE")
        op.create_index(f"ix_{TOKENS}_oauth_client_id", TOKENS, ["oauth_client_id"])
    if "refresh_hash" not in columns:
        op.add_column(TOKENS, sa.Column("refresh_hash", sa.String(128), nullable=True))
        op.create_index(f"ix_{TOKENS}_refresh_hash", TOKENS, ["refresh_hash"], unique=True)
    if "refresh_expires_at" not in columns:
        op.add_column(TOKENS, sa.Column("refresh_expires_at", sa.DateTime(timezone=True),
                                        nullable=True))


def downgrade() -> None:
    op.drop_column(TOKENS, "refresh_expires_at")
    op.drop_index(f"ix_{TOKENS}_refresh_hash", TOKENS)
    op.drop_column(TOKENS, "refresh_hash")
    op.drop_index(f"ix_{TOKENS}_oauth_client_id", TOKENS)
    op.drop_constraint(f"{TOKENS}_oauth_client_id_fkey", TOKENS, type_="foreignkey")
    op.drop_column(TOKENS, "oauth_client_id")
    op.drop_table("oauth_codes")
    op.drop_table("oauth_clients")
