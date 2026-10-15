"""R7.1/R7.3 - a caller only reaches the projects and models it was granted."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models import ApiKey, DriftWindow, Model, Project
from app.services.gateway import InputImage
from app.services.workflow import WorkflowEngine

from .conftest import _person


@pytest.fixture
async def other(sessionmaker) -> Project:
    """A project nobody in the default fixtures belongs to."""
    async with sessionmaker() as db:
        project = Project(slug="other", name="Other")
        db.add(project)
        await db.commit()
        return project


def _into(headers: dict[str, str], slug: str) -> dict[str, str]:
    return {**headers, "x-aperture-project": slug}


async def test_project_header_does_not_grant_membership(client, admin, other):
    response = await client.get("/v1/models", headers=_into(admin.headers, "other"))
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "forbidden"


async def test_predict_refuses_a_project_the_caller_is_not_in(client, admin, other):
    response = await client.post("/v1/models/anything/production/predict",
                                 headers=_into(admin.headers, "other"))
    assert response.status_code == 403, response.text


async def test_workflow_run_refuses_a_project_the_caller_is_not_in(client, admin, other):
    response = await client.post("/v1/workflows/anything/run",
                                 headers=_into(admin.headers, "other"))
    assert response.status_code == 403, response.text


async def test_superadmin_reaches_any_project(client, db, project, other):
    root = await _person(db, project, "root@test", "viewer", superadmin=True)
    response = await client.get("/v1/models", headers=_into(root.headers, "other"))
    assert response.status_code == 200, response.text


async def test_baseline_cannot_be_set_on_another_projects_version(client, admin, db, other):
    now = datetime.now(UTC)
    db.add(DriftWindow(project_id=other.id, model_id="m", version_id="v-other",
                       window_start=now, window_end=now, stats={}))
    await db.commit()
    response = await client.post("/v1/drift/v-other/baseline", headers=admin.headers)
    assert response.status_code == 404, response.text


async def test_workflow_model_node_honours_key_scope(db, project):
    allowed = Model(project_id=project.id, slug="model-a", name="A", task="detect")
    hidden = Model(project_id=project.id, slug="model-b", name="B", task="detect")
    key = ApiKey(project_id=project.id, name="scoped", prefix="ap_test", key_hash="x",
                 scopes=["model-a"])
    db.add_all([allowed, hidden, key])
    await db.commit()

    graph = {
        "nodes": [{"id": "in", "data": {"type": "input"}},
                  {"id": "m", "data": {"type": "model", "model": "model-b"}}],
        "edges": [{"source": "in", "target": "m"}],
    }
    engine = WorkflowEngine(db, project, request_id="r", api_key=key)
    result = await engine.run(graph, [InputImage(data=b"x", name="a.jpg", source="test")])
    assert result.status == "error"
    assert result.error_code == "key_not_scoped_to_model"
    assert result.failed_node == "m"
