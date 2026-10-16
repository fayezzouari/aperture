"""Upload sessions remember the version they were opened for.

Before this, completing an upload attached the weights to the model's newest
draft, so a version created while a long upload was running stole its weights.

Guarded like 0002 and 0003: the API's startup runs Base.metadata.create_all,
which adds missing tables but never columns, so on a fresh install the column is
already there and on an existing one it is not.

Revision ID: 0004_upload_version
Revises: 0003_drop_projects
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_upload_version"
down_revision = "0003_drop_projects"
branch_labels = None
depends_on = None

TABLE = "upload_sessions"


def upgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns(TABLE)}
    if "version_id" in columns:
        return
    # nullable: sessions opened before this keep the old newest-draft fallback
    op.add_column(TABLE, sa.Column("version_id", sa.String(36), nullable=True))
    # the names Postgres and create_all give these, so both install paths match
    op.create_foreign_key(f"{TABLE}_version_id_fkey", TABLE, "model_versions",
                          ["version_id"], ["id"], ondelete="CASCADE")
    op.create_index(f"ix_{TABLE}_version_id", TABLE, ["version_id"])


def downgrade() -> None:
    # dropping the column takes its foreign key and index with it
    op.drop_column(TABLE, "version_id")
