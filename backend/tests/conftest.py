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
from app.models import Base, Membership, Project, User
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
    headers: dict[str, str]  # a session JWT plus the default project header


async def _person(db: AsyncSession, project: Project, email: str, role: str,
                  superadmin: bool = False) -> Person:
    user = User(email=email, name=email.split("@")[0], password_hash=hash_password("pw"),
                is_superadmin=superadmin)
    db.add(user)
    await db.flush()
    db.add(Membership(user_id=user.id, project_id=project.id, role=role))
    await db.commit()
    token, _ = issue_token(user, {project.id: role})
    return Person(user=user, headers={"authorization": f"Bearer {token}",
                                      "x-aperture-project": project.slug})


@pytest.fixture
async def project(sessionmaker) -> Project:
    async with sessionmaker() as db:
        project = Project(slug="default", name="Default")
        db.add(project)
        await db.commit()
        return project


@pytest.fixture
async def admin(db, project) -> Person:
    return await _person(db, project, "admin@test", "admin")


@pytest.fixture
async def viewer(db, project) -> Person:
    return await _person(db, project, "viewer@test", "viewer")


async def make_pat(client: httpx.AsyncClient, headers: dict[str, str], name: str = "agent",
                   **body) -> dict:
    response = await client.post("/v1/auth/tokens", json={"name": name, **body}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def bearer(token: str, project: str = "default") -> dict[str, str]:
    return {"authorization": f"Bearer {token}", "x-aperture-project": project}
