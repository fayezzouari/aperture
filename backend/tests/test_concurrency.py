"""Concurrent writers: reconcile, version numbers and on-demand deployments.

The SQLite tests cover the single-process behaviour and the 409 handler. The
races themselves only exist on a real database with real row locks, so those
tests run against Postgres when AP_TEST_PG_URL points at a throwaway database
(they create and drop every table), and are skipped otherwise:

    docker run -d --name ap-pg-test -e POSTGRES_PASSWORD=pw -p 55497:5432 postgres:16-alpine
    AP_TEST_PG_URL=postgresql+asyncpg://postgres:pw@localhost:55497/postgres pytest -q
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

import httpx
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.deps import Principal
from app.errors import install_handlers
from app.models import Assignment, Base, Deployment, Model, ModelVersion, Runtime, User
from app.services import deployer, ondemand
from app.services.versioning import next_version_number

PG_URL = os.environ.get("AP_TEST_PG_URL")
needs_pg = pytest.mark.skipif(not PG_URL, reason="set AP_TEST_PG_URL to run against Postgres")


async def _seed(session: AsyncSession, runtimes: int = 3, replicas: int = 2) -> tuple[Model, ModelVersion]:
    model = Model(slug="cells", name="Cells", task="detection")
    session.add(model)
    await session.flush()
    version = ModelVersion(model_id=model.id, version=1, status="ready", config={},
                           artifact_format="onnx", artifact_info={"usability": "servable"})
    session.add(version)
    await session.flush()
    session.add(Deployment(model_id=model.id, version_id=version.id, mode="primary",
                           status="pending", replicas=replicas))
    now = datetime.now(UTC)
    for i in range(runtimes):
        session.add(Runtime(id=f"rt-{i}", url=f"http://rt-{i}", status="ready",
                            last_heartbeat=now))
    await session.commit()
    return model, version


# ------------------------------------------------------------------ SQLite


async def test_integrity_error_becomes_a_409_conflict():
    app = FastAPI()
    install_handlers(app)

    @app.get("/boom")
    async def boom():
        raise IntegrityError("INSERT ...", {}, Exception("UNIQUE constraint failed"))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/boom")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


async def test_reconcile_places_each_replica_once(sessionmaker):
    async with sessionmaker() as session:
        await _seed(session)
    async with sessionmaker() as session:
        result = await deployer.reconcile(session)
    assert result["assignments_created"] == 2
    async with sessionmaker() as session:
        rows = (await session.execute(sa.select(Assignment.runtime_id))).scalars().all()
    assert len(rows) == len(set(rows)) == 2


async def test_a_reconcile_already_running_makes_the_next_one_skip(sessionmaker):
    async with sessionmaker() as session:
        await _seed(session)
    async with deployer._local_lock:
        async with sessionmaker() as session:
            result = await deployer.reconcile(session)
    assert result["skipped"] is True


async def test_shadow_and_primary_of_one_version_never_share_a_runtime(sessionmaker):
    # uq_assign_runtime_version holds per (runtime, version), across deployments
    async with sessionmaker() as session:
        model, version = await _seed(session, runtimes=2, replicas=2)
        session.add(Deployment(model_id=model.id, version_id=version.id, mode="shadow",
                               status="pending", replicas=2))
        await session.commit()
    async with sessionmaker() as session:
        await deployer.reconcile(session)  # used to die on the unique constraint
    async with sessionmaker() as session:
        pairs = (await session.execute(
            sa.select(Assignment.runtime_id, Assignment.version_id))).all()
    assert len(pairs) == len(set(pairs)) == 2


# ---------------------------------------------------------------- Postgres


@pytest.fixture
async def pg():
    engine = create_async_engine(PG_URL, pool_size=20)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@needs_pg
async def test_concurrent_version_creates_get_distinct_numbers(pg):
    async with pg() as session:
        model, _ = await _seed(session, runtimes=0)

    async def create() -> int:
        async with pg() as session:
            number = await next_version_number(session, model.id)
            await asyncio.sleep(0.05)  # widen the window between read and write
            session.add(ModelVersion(model_id=model.id, version=number, config={}))
            await session.commit()
            return number

    numbers = await asyncio.gather(*(create() for _ in range(8)))
    assert sorted(numbers) == list(range(2, 10))


@needs_pg
async def test_concurrent_reconciles_never_double_place(pg, monkeypatch):
    async with pg() as session:
        await _seed(session, runtimes=3, replicas=2)

    # Line every pass up just before it reads the assignments, so without the
    # lock they all see none and all insert. With the lock only one pass gets
    # here; the barrier times out for it and it carries on.
    read_runtimes = deployer._fresh_runtimes
    barrier = asyncio.Barrier(10)

    async def lined_up(session):
        found = await read_runtimes(session)
        try:
            await asyncio.wait_for(barrier.wait(), 0.5)
        except (TimeoutError, asyncio.BrokenBarrierError):
            pass
        return found

    monkeypatch.setattr(deployer, "_fresh_runtimes", lined_up)

    async def run():
        async with pg() as session:
            return await deployer.reconcile(session)

    results = await asyncio.gather(*(run() for _ in range(10)))
    created = sum(r.get("assignments_created", 0) for r in results)
    async with pg() as session:
        rows = (await session.execute(sa.select(Assignment.runtime_id))).scalars().all()
    assert created == len(rows) == 2
    assert len(set(rows)) == 2


@needs_pg
async def test_concurrent_on_demand_starts_share_one_deployment(pg):
    async with pg() as session:
        model = Model(slug="cells", name="Cells", task="detection")
        session.add(model)
        await session.flush()
        version = ModelVersion(model_id=model.id, version=1, status="ready", config={},
                               artifact_format="onnx", artifact_info={"usability": "servable"})
        user = User(email="dev@test", name="dev", role="developer")
        session.add_all([version, user])
        await session.commit()
    principal = Principal(kind="user", id=user.id, label=user.email, role="developer")

    async def start():
        async with pg() as session:
            m = await session.get(Model, model.id)
            v = await session.get(ModelVersion, version.id)
            try:
                await ondemand.ensure_serving(session, m, v, principal, timeout=0.01)
            except Exception as exc:  # no runtime will ever be ready; only that is expected
                assert getattr(exc, "code", None) == "no_replica_available", exc

    await asyncio.gather(*(start() for _ in range(6)))
    async with pg() as session:
        count = (await session.execute(
            sa.select(sa.func.count()).select_from(Deployment)
            .where(Deployment.version_id == version.id))).scalar_one()
    assert count == 1
