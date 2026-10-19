"""What belongs to a workspace when the row has no workspace of its own.

Versions, deployments, inference logs, drift windows and uploads hang off a
model, and a model belongs to one workspace; so they are filtered through it.
"""

from __future__ import annotations

import sqlalchemy as sa

from app.models import Model


def models_in(workspace_id: str | None) -> sa.Select:
    """The ids of a workspace's models, for `column.in_(...)`."""
    return sa.select(Model.id).where(Model.workspace_id == workspace_id)
