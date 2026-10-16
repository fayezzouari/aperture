"""Job rows never hand a credential back to whoever can read them."""

from __future__ import annotations

from app.models import Job


async def test_roboflow_import_key_is_never_returned(client, admin, viewer, db):
    # a row written before the key moved onto the queued task still holds it in params
    job = Job(kind="roboflow_import", status="queued",
              created_by=admin.user.id,
              params={"ref": "ws/proj/3", "api_key": "rf_live_secret", "workspace": "ws",
                      "nested": {"token": "abc"}, "slug": "imported"})
    db.add(job)
    await db.commit()

    listed = await client.get("/v1/jobs", headers=viewer.headers)
    assert listed.status_code == 200, listed.text
    one = await client.get(f"/v1/jobs/{job.id}", headers=viewer.headers)
    assert one.status_code == 200, one.text

    for body in (listed.text, one.text):
        assert "rf_live_secret" not in body
        assert "abc" not in body
    params = one.json()["params"]
    assert params["api_key"] == "***"
    assert params["slug"] == "imported"  # ordinary fields survive


async def test_import_route_keeps_the_key_off_the_row(client, admin, db, monkeypatch):
    from app.jobs import queue

    sent = {}

    async def fake_enqueue(task, *args, **kwargs):
        sent.update(task=task, args=args, kwargs=kwargs)
        return "job-1"

    async def alive(_queue):
        return True

    monkeypatch.setattr(queue, "enqueue", fake_enqueue)
    monkeypatch.setattr(queue, "worker_alive", alive)

    response = await client.post("/v1/imports/roboflow",
                                 json={"ref": "ws/proj/3", "api_key": "rf_live_secret"},
                                 headers=admin.headers)
    assert response.status_code == 202, response.text
    assert "rf_live_secret" not in response.text

    row = await db.get(Job, response.json()["id"])
    assert "api_key" not in row.params
    assert sent["kwargs"]["api_key"] == "rf_live_secret"
