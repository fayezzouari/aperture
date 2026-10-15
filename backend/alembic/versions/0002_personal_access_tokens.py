"""Personal access tokens (R8.1).

Guarded with has_table because the API's startup runs Base.metadata.create_all,
so on a running install this table already exists by the time `alembic upgrade
head` gets here.

Revision ID: 0002_personal_access_tokens
Revises: 0001_baseline
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_personal_access_tokens"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

TABLE = "personal_access_tokens"


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table(TABLE):
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("prefix", sa.String(24), nullable=False),
        sa.Column("token_hash", sa.String(128), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
    )
    op.create_index(f"ix_{TABLE}_user_id", TABLE, ["user_id"])
    op.create_index(f"ix_{TABLE}_prefix", TABLE, ["prefix"], unique=True)
    op.create_index(f"ix_{TABLE}_created_at", TABLE, ["created_at"])


def downgrade() -> None:
    op.drop_table(TABLE)
