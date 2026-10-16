"""R4.1/R4.5 - placement only hands a runtime the versions its memory can hold."""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa

from app.models import Assignment, Deployment, Model, ModelVersion, Runtime
from app.services.deployer import apply_runtime_report, reconcile

MB = 1024 * 1024


async def _runtime(db, runtime_id: str, available_mb: int | None, loaded=None) -> Runtime:
    metrics = {"formats": ["onnx", "ort"]}
    if available_mb is not None:
        metrics["memory"] = {"budget_mb": 4000, "used_mb": 4000 - available_mb,
                             "available_mb": available_mb}
    runtime = Runtime(id=runtime_id, url=f"http://{runtime_id}:9100", status="ready",
                      device="cpu", metrics=metrics, loaded=loaded or [],
                      last_heartbeat=datetime.now(UTC))
    db.add(runtime)
    await db.commit()
    return runtime


async def _deploy(db, slug: str, weights_mb: int, replicas: int = 1) -> Deployment:
    model = Model(slug=slug, name=slug, task="detect")
    db.add(model)
    await db.flush()
    version = ModelVersion(model_id=model.id, version=1, artifact_format="onnx",
                           weights_key=f"{slug}/v1/model.onnx", weights_bytes=weights_mb * MB,
                           status="ready")
    db.add(version)
    await db.flush()
    deployment = Deployment(model_id=model.id, version_id=version.id, replicas=replicas,
                            status="pending")
    db.add(deployment)
    await db.commit()
    return deployment


async def _placed(db, deployment: Deployment) -> list[str]:
    rows = await db.execute(sa.select(Assignment.runtime_id)
                            .where(Assignment.deployment_id == deployment.id))
    return sorted(rows.scalars().all())


async def test_a_version_goes_only_where_it_fits(db):
    await _runtime(db, "small", available_mb=500)
    await _runtime(db, "roomy", available_mb=3000)
    big = await _deploy(db, "big", weights_mb=400)  # ~1200 MB at the 3x estimate

    await reconcile(db)
    assert await _placed(db, big) == ["roomy"]


async def test_too_big_for_every_runtime_waits_and_says_why(db):
    await _runtime(db, "a", available_mb=800)
    await _runtime(db, "b", available_mb=900)
    huge = await _deploy(db, "huge", weights_mb=1000)  # ~3000 MB

    await reconcile(db)
    await db.refresh(huge)
    assert await _placed(db, huge) == []
    assert huge.status == "pending"
    assert huge.status_detail.startswith("waiting for a runtime with enough memory (needs ~3000 MB")
    assert "900 MB" in huge.status_detail


async def test_pending_placements_count_against_the_runtime(db):
    await _runtime(db, "only", available_mb=1300)
    first = await _deploy(db, "first", weights_mb=200)   # ~600 MB
    second = await _deploy(db, "second", weights_mb=200)
    third = await _deploy(db, "third", weights_mb=200)

    await reconcile(db)
    placed = [await _placed(db, d) for d in (first, second, third)]
    # two fit in 1300 MB; the third would oversubscribe the runtime before it reports back
    assert sorted(len(p) for p in placed) == [0, 1, 1]


async def test_a_measured_version_is_placed_by_its_measurement(db):
    huge = await _deploy(db, "measured", weights_mb=1000)  # 3000 MB if guessed
    loaded = [{"version_id": huge.version_id, "state": "ready", "memory_mb": 700}]
    await _runtime(db, "holder", available_mb=100, loaded=loaded)
    await _runtime(db, "spare", available_mb=800)
    huge.replicas = 2
    await db.commit()

    await reconcile(db)
    # the second replica fits on "spare" because the version is known to cost 700 MB
    assert "spare" in await _placed(db, huge)


async def test_a_refusal_moves_the_replica_and_never_fails_the_version(db):
    a = await _runtime(db, "a", available_mb=2000)
    await _runtime(db, "b", available_mb=500)
    dep = await _deploy(db, "moving", weights_mb=300)  # ~900 MB
    await reconcile(db)
    assert await _placed(db, dep) == ["a"]

    # "a" filled up meanwhile and refuses; it now has 200 MB, "c" joins with room
    refusal = {"version_id": dep.version_id, "state": "failed", "needs_mb": 900,
               "detail": "insufficient memory: needs ~900 MB, 200 MB free (budget 4000 MB)"}
    a.loaded = [refusal]
    a.metrics = {**a.metrics, "memory": {"budget_mb": 4000, "used_mb": 3800,
                                         "available_mb": 200}}
    await db.commit()
    await apply_runtime_report(db, "a", [refusal])
    await _runtime(db, "c", available_mb=1500)

    await reconcile(db)
    version = await db.get(ModelVersion, dep.version_id)
    assert await _placed(db, dep) == ["c"]
    assert version.status == "ready"  # a full runtime says nothing about the weights


async def test_runtimes_that_do_not_report_memory_are_still_used(db):
    await _runtime(db, "legacy", available_mb=None)
    dep = await _deploy(db, "anywhere", weights_mb=5000)

    await reconcile(db)
    assert await _placed(db, dep) == ["legacy"]
