"""Turning ORM rows into plain dicts the response models can validate.

`obj.__dict__` is not enough: a row that was just inserted has no value loaded
for any column the database filled in (server defaults like created_at, and
columns left at NULL), so serialising straight from it drops fields the response
model requires. This helper loads what is missing first, once, and then reads
every mapped column explicitly.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession


async def dump(session: AsyncSession, obj: Any, **extra: Any) -> dict:
    state = sa_inspect(obj)
    if state.unloaded:
        await session.refresh(obj)
    data = {attribute.key: getattr(obj, attribute.key) for attribute in state.mapper.column_attrs}
    data.update(extra)
    return data


def dump_loaded(obj: Any, **extra: Any) -> dict:
    """For rows that came straight out of a SELECT, where nothing is unloaded."""
    state = sa_inspect(obj)
    data = {attribute.key: getattr(obj, attribute.key) for attribute in state.mapper.column_attrs}
    data.update(extra)
    return data
