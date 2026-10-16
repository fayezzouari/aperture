"""Version numbers: max + 1, allocated one writer at a time per model.

A version number is read as max(version) + 1 and written later, so two writers
on the same model (two uploads, or a conversion job finishing while someone
creates a draft) would both pick the same number and one would die on
uq_version_model_number. Locking the parent Model row first makes the second
writer wait for the first to commit, then read the number after it.

The lock lives until the caller's transaction ends, so callers should commit
soon after inserting. On SQLite, used in tests, FOR UPDATE is a no-op.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Model, ModelVersion


async def lock_model(session: AsyncSession, model_id: str) -> None:
    await session.execute(sa.select(Model.id).where(Model.id == model_id).with_for_update())


async def next_version_number(session: AsyncSession, model_id: str) -> int:
    await lock_model(session, model_id)
    latest = (
        await session.execute(
            sa.select(sa.func.max(ModelVersion.version)).where(ModelVersion.model_id == model_id)
        )
    ).scalar_one()
    return int(latest or 0) + 1
