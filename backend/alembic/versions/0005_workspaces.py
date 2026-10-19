"""Workspaces: models and workflows belong to one, users are members with a role.

A workspace is a context inside the instance - an install can keep a `cloud`
workspace apart from an `edge` one, each with its own models, workflows and
members. Everything that hangs off a model (versions, deployments, inference
logs, drift, jobs, uploads) follows its model's workspace without a column of
its own. API keys, reports, alerts, audit and capture settings stay instance-wide.

Guarded like 0002-0004: the API's startup runs Base.metadata.create_all, which
adds missing tables but never columns. A fresh install therefore arrives here
already in the new shape and starts with no workspace at all; the first admin
creates one on sign-in.

What happens to an existing install:

* every model and workflow moves into one new workspace, `General`, which can
  be renamed afterwards
* every user who is not an instance admin becomes a member of it with their
  instance role, so nobody loses what they could see before; instance admins
  need no membership
* model and workflow slugs become unique per workspace instead of per instance

Revision ID: 0005_workspaces
Revises: 0004_upload_version
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa

from alembic import op

revision = "0005_workspaces"
down_revision = "0004_upload_version"
branch_labels = None
depends_on = None

SCOPED = {"models": "uq_model_workspace_slug", "workflows": "uq_workflow_workspace_slug"}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    def columns(table: str) -> set[str]:
        return {c["name"] for c in inspector.get_columns(table)}

    if "workspaces" not in tables:
        op.create_table(
            "workspaces",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("slug", sa.String(64), nullable=False, unique=True),
            sa.Column("name", sa.String(160), nullable=False),
            sa.Column("description", sa.Text, nullable=True),
            *_timestamps(),
        )
        op.create_index("ix_workspaces_created_at", "workspaces", ["created_at"])
    if "workspace_members" not in tables:
        op.create_table(
            "workspace_members",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(36),
                      sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("workspace_id", sa.String(36),
                      sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
            sa.Column("role", sa.String(16), nullable=False, server_default="viewer"),
            *_timestamps(),
            sa.UniqueConstraint("user_id", "workspace_id", name="uq_member_user_workspace"),
        )
        for column in ("user_id", "workspace_id", "created_at"):
            op.create_index(f"ix_workspace_members_{column}", "workspace_members", [column])

    if "last_workspace_id" not in columns("users"):
        op.add_column("users", sa.Column("last_workspace_id", sa.String(36), nullable=True))
        op.create_foreign_key("users_last_workspace_id_fkey", "users", "workspaces",
                              ["last_workspace_id"], ["id"], ondelete="SET NULL")

    pending = [t for t in SCOPED if "workspace_id" not in columns(t)]
    if not pending:
        return

    general = None
    if any(bind.execute(sa.text(f"SELECT 1 FROM {t} LIMIT 1")).first() for t in pending):
        general = str(uuid.uuid4())
        bind.execute(
            sa.text("INSERT INTO workspaces (id, slug, name, description) "
                    "VALUES (:id, 'general', 'General', :description)"),
            {"id": general, "description": "Everything registered before workspaces existed."},
        )
        bind.execute(
            sa.text("INSERT INTO workspace_members (id, user_id, workspace_id, role) "
                    "SELECT gen_random_uuid()::text, id, :ws, role "
                    "FROM users WHERE role <> 'admin'"),
            {"ws": general},
        )

    for table in pending:
        op.add_column(table, sa.Column("workspace_id", sa.String(36), nullable=True))
        if general is not None:
            bind.execute(sa.text(f"UPDATE {table} SET workspace_id = :ws"), {"ws": general})
        op.alter_column(table, "workspace_id", nullable=False)
        op.create_foreign_key(f"{table}_workspace_id_fkey", table, "workspaces",
                              ["workspace_id"], ["id"], ondelete="CASCADE")
        op.create_index(f"ix_{table}_workspace_id", table, ["workspace_id"])
        # the instance-wide slug unique from 0003 gives way to one per workspace
        for unique in sa.inspect(bind).get_unique_constraints(table):
            if unique["column_names"] == ["slug"]:
                op.drop_constraint(unique["name"], table, type_="unique")
        op.create_unique_constraint(SCOPED[table], table, ["workspace_id", "slug"])


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
    )


def downgrade() -> None:
    raise NotImplementedError("workspaces cannot be merged back into one instance")
