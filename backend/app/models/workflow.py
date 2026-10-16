"""The `Workflow` table."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import JSON_T, Base, TimestampMixin, new_id


class Workflow(Base, TimestampMixin):
    """A DAG of model calls and transforms, exposed as one endpoint.

    The graph is a plain JSON document ({nodes, edges}) so the visual editor and
    the execution engine share one representation - what you drew is literally
    what runs. `revision` increments on publish, and every run records the
    revision it executed, which is what makes a result traceable back to a
    specific graph.
    """

    __tablename__ = "workflows"
    __table_args__ = (sa.UniqueConstraint("slug", name="uq_workflow_slug"),)

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_id)
    slug: Mapped[str] = mapped_column(sa.String(96), nullable=False, index=True)
    name: Mapped[str] = mapped_column(sa.String(160), nullable=False)
    description: Mapped[str] = mapped_column(sa.Text, default="")
    owner: Mapped[str] = mapped_column(sa.String(160), default="")
    graph: Mapped[dict] = mapped_column(JSON_T, default=dict)          # the draft
    published_graph: Mapped[dict | None] = mapped_column(JSON_T)        # what runs
    revision: Mapped[int] = mapped_column(sa.Integer, default=0)
    status: Mapped[str] = mapped_column(sa.String(16), default="draft")  # draft|published
    labels: Mapped[list] = mapped_column(JSON_T, default=list)
    created_by: Mapped[str | None] = mapped_column(sa.String(36))
    published_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
