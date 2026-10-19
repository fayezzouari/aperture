"""Async engine + session plumbing."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base
from app.settings import settings

engine = create_async_engine(
    settings.database_url,
    pool_size=20,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=1800,
    echo=False,
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def create_schema() -> None:
    """Create whatever tables are missing, for a database alembic has not been run on.

    The API container migrates before it starts (serve.sh), so this is normally
    a no-op; it keeps a bare `uvicorn` run and the tests working without alembic.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
