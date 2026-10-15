"""R3.8 - a job reaches the worker only once its row is committed."""

from __future__ import annotations

import sqlalchemy as sa

from app.jobs import queue
from app.models import AuditLog, Job


async def test_row_is_committed_before_the_worker_hears_of_it(client, admin, sessionmaker,
                                                               monkeypatch):
    seen = []

    async def fake_enqueue(task, job_id, **kwargs):
        # what the worker would do: look the row up from a connection of its own
        async with sessionmaker() as worker:
            job = await worker.get(Job, job_id)
            audited = (await worker.execute(
                sa.select(AuditLog).where(AuditLog.target_id == job_id))).scalars().all()
        seen.append((task, job and job.status, len(audited)))
        return "arq-id"

    monkeypatch.setattr(queue, "enqueue", fake_enqueue)
    response = await client.post("/v1/jobs/exports/flagged", headers=admin.headers)
    assert response.status_code == 202, response.text
    assert seen == [("export_flagged_dataset", "queued", 1)]


async def test_unreachable_queue_fails_the_job_instead_of_leaving_it_queued(
        client, admin, db, monkeypatch):
    async def broken_enqueue(task, job_id, **kwargs):
        raise ConnectionError("redis is down")

    monkeypatch.setattr(queue, "enqueue", broken_enqueue)
    response = await client.post("/v1/jobs/exports/flagged", headers=admin.headers)
    assert response.status_code == 503, response.text
    body = response.json()["error"]
    assert body["code"] == "runtime_unhealthy"

    job = await db.get(Job, body["details"]["job_id"])
    assert job.status == "failed"
    assert "redis is down" in job.error
    assert job.finished_at is not None
