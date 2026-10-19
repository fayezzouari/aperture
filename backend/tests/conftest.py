"""Backend test fixtures: the FastAPI app over an in-process SQLite file.

The lifespan is deliberately not run (it pings Redis and MinIO), so anything a
test touches has to be reachable through the ORM alone. `get_session` is
overridden with a sessionmaker on a throwaway SQLite database; the module-level
Postgres engine in `app.db` is created lazily and never connects.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db import get_session
from app.main import app
from app.models import Base, User, Workspace, WorkspaceMember
from app.security import hash_password, issue_token


@pytest.fixture
def engine(tmp_path):
    # one database per test: cheap on SQLite, and no fixture has to clean up after itself
    return create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")


@pytest.fixture
async def sessionmaker(engine):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    await engine.dispose()


@pytest.fixture(autouse=True)
def _override_session(sessionmaker):
    async def _session() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    app.dependency_overrides[get_session] = _session
    yield
    app.dependency_overrides.pop(get_session, None)


@pytest.fixture
async def db(sessionmaker) -> AsyncIterator[AsyncSession]:
    async with sessionmaker() as session:
        yield session


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@dataclass
class Person:
    user: User
    headers: dict[str, str]  # a session JWT


async def add_workspace(session: AsyncSession, slug: str = "main") -> Workspace:
    """A workspace to hang test models on, for tests that build rows by hand."""
    row = Workspace(slug=slug, name=slug.title())
    session.add(row)
    await session.flush()
    return row


@pytest.fixture
async def workspace(db) -> Workspace:
    """The one workspace the people fixtures belong to; their instance role is their role in it."""
    row = await add_workspace(db)
    await db.commit()
    return row


async def _person(db: AsyncSession, email: str, role: str,
                  workspace: Workspace | None = None) -> Person:
    user = User(email=email, name=email.split("@")[0], password_hash=hash_password("pw"),
                role=role)
    db.add(user)
    await db.flush()
    if workspace is not None and role != "admin":
        db.add(WorkspaceMember(user_id=user.id, workspace_id=workspace.id, role=role))
    await db.commit()
    token, _ = issue_token(user)
    return Person(user=user, headers={"authorization": f"Bearer {token}"})


@pytest.fixture
async def admin(db, workspace) -> Person:
    return await _person(db, "admin@test", "admin", workspace)


@pytest.fixture
async def developer(db, workspace) -> Person:
    return await _person(db, "developer@test", "developer", workspace)


@pytest.fixture
async def viewer(db, workspace) -> Person:
    return await _person(db, "viewer@test", "viewer", workspace)


async def make_pat(client: httpx.AsyncClient, headers: dict[str, str], name: str = "agent",
                   **body) -> dict:
    response = await client.post("/v1/auth/tokens", json={"name": name, **body}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def bearer(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}
