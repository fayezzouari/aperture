"""One instance, no projects: roles move onto users, capture overrides onto the instance.

Dev and prod are separated with deployment stages and tags, so the project
boundary goes. Every step is guarded on what already exists: the API's startup
runs Base.metadata.create_all, and 0001 builds the schema from the current
models, so a fresh database arrives here already in the new shape.

What happens to an existing install:

* each user's role becomes the highest role they held in any project, and an
  instance admin (is_superadmin) becomes `admin`; a user with no membership is
  a `viewer`
* the oldest live project's capture sample rate and retention move to the
  single `instance_settings` row
* `project_id` goes from every table; model and workflow slugs become unique
  across the instance
* `memberships`, `projects` and `users.is_superadmin` are dropped

Several projects can be merged only if no model or workflow slug repeats
across them. If one does, nothing is changed and the upgrade stops with the list.

Revision ID: 0003_drop_projects
Revises: 0002_personal_access_tokens
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_drop_projects"
down_revision = "0002_personal_access_tokens"
branch_labels = None
depends_on = None

SCOPED_TABLES = (
    "alert_events", "alert_rules", "api_keys", "audit_logs", "deployments", "drift_windows",
    "inference_logs", "jobs", "models", "report_schedules", "upload_sessions", "workflows",
    "workflow_runs",
)
GLOBAL_SLUGS = {"models": "uq_model_slug", "workflows": "uq_workflow_slug"}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    def columns(table: str) -> set[str]:
        return {c["name"] for c in inspector.get_columns(table)} if table in tables else set()

    if "projects" in tables:
        _refuse_slug_collisions(bind)

    # 1. roles move onto users
    if "role" not in columns("users"):
        op.add_column("users", sa.Column("role", sa.String(16), nullable=False,
                                         server_default="viewer"))
    if "memberships" in tables:
        bind.execute(sa.text("""
            UPDATE users SET role = CASE m.rank WHEN 2 THEN 'admin'
                                                WHEN 1 THEN 'developer'
                                                ELSE 'viewer' END
            FROM (SELECT user_id,
                         max(CASE role WHEN 'admin' THEN 2 WHEN 'developer' THEN 1 ELSE 0 END)
                           AS rank
                  FROM memberships GROUP BY user_id) AS m
            WHERE users.id = m.user_id
        """))
    if "is_superadmin" in columns("users"):
        bind.execute(sa.text("UPDATE users SET role = 'admin' WHERE is_superadmin"))

    # 2. capture overrides move onto the instance
    if "instance_settings" not in tables:
        op.create_table(
            "instance_settings",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("capture_sample_rate", sa.Float, nullable=True),
            sa.Column("capture_retention_days", sa.Integer, nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                      nullable=False),
        )
    if "projects" in tables:
        exists = bind.execute(sa.text("SELECT 1 FROM instance_settings WHERE id = 1")).first()
        if exists is None:
            bind.execute(sa.text("""
                INSERT INTO instance_settings (id, capture_sample_rate, capture_retention_days)
                SELECT 1, capture_sample_rate, capture_retention_days FROM projects
                ORDER BY (archived_at IS NOT NULL), created_at LIMIT 1
            """))

    # 3. project_id goes; Postgres drops the column's FK, its indexes and the
    #    (project_id, slug) uniques along with it
    for table in SCOPED_TABLES:
        if "project_id" in columns(table):
            op.drop_column(table, "project_id")
    for table, name in GLOBAL_SLUGS.items():
        if table not in tables:
            continue
        existing = {u["name"] for u in sa.inspect(bind).get_unique_constraints(table)}
        if name not in existing:
            op.create_unique_constraint(name, table, ["slug"])

    # 4. the tables nothing points at any more
    if "memberships" in tables:
        op.drop_table("memberships")
    if "projects" in tables:
        op.drop_table("projects")
    if "is_superadmin" in columns("users"):
        op.drop_column("users", "is_superadmin")


def _refuse_slug_collisions(bind) -> None:
    clashes = []
    for table in GLOBAL_SLUGS:
        rows = bind.execute(sa.text(
            f"SELECT slug FROM {table} GROUP BY slug HAVING count(*) > 1 ORDER BY slug"
        )).scalars().all()
        clashes += [f"{table}.{slug}" for slug in rows]
    if clashes:
        raise RuntimeError(
            "cannot merge projects: these slugs exist in more than one project - rename "
            "them so each is unique, then run the upgrade again: " + ", ".join(clashes)
        )


def downgrade() -> None:
    raise NotImplementedError("projects cannot be restored")
