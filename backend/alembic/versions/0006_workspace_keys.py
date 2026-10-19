"""API keys belong to one workspace.

An instance-wide key resolved model slugs across every workspace, so creating a
model in one workspace could make another workspace's callers ambiguous without
anything changing on their side. A key now reaches only its own workspace.

Guarded like 0005: on a fresh install create_all already made the column.

What happens to an existing install's keys: they move into the `general`
workspace that 0005 made for the models they were calling; failing that, the
oldest workspace; failing that (keys but never a model), a new `General`.

Revision ID: 0006_workspace_keys
Revises: 0005_workspaces
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op

revision = "0006_workspace_keys"
down_revision = "0005_workspaces"
branch_labels = None
depends_on = None

TABLE = "api_keys"


def upgrade() -> None:
    bind = op.get_bind()
    if "workspace_id" in {c["name"] for c in sa.inspect(bind).get_columns(TABLE)}:
        return
    op.add_column(TABLE, sa.Column("workspace_id", sa.String(36), nullable=True))

    if bind.execute(sa.text(f"SELECT 1 FROM {TABLE} LIMIT 1")).first():
        home = bind.execute(sa.text(
            "SELECT id FROM workspaces ORDER BY (slug <> 'general'), created_at LIMIT 1"
        )).scalar()
        if home is None:
            home = str(uuid.uuid4())
            bind.execute(
                sa.text("INSERT INTO workspaces (id, slug, name, description) "
                        "VALUES (:id, 'general', 'General', :description)"),
                {"id": home, "description": "Everything registered before workspaces existed."},
            )
        bind.execute(sa.text(f"UPDATE {TABLE} SET workspace_id = :ws"), {"ws": home})

    op.alter_column(TABLE, "workspace_id", nullable=False)
    op.create_foreign_key(f"{TABLE}_workspace_id_fkey", TABLE, "workspaces",
                          ["workspace_id"], ["id"], ondelete="CASCADE")
    op.create_index(f"ix_{TABLE}_workspace_id", TABLE, ["workspace_id"])


def downgrade() -> None:
    op.drop_column(TABLE, "workspace_id")
