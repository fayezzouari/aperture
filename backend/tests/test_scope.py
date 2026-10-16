"""R7.1/R7.2 - a caller reaches what its role allows, and an API key only its models."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import ApiKey, Model
from app.services.gateway import InputImage
from app.services.workflow import WorkflowEngine


async def test_old_project_header_is_ignored(client, viewer):
    """Clients written for the multi-project API still send the header; it changes nothing."""
    response = await client.get("/v1/models",
                                headers={**viewer.headers, "x-aperture-project": "anything"})
    assert response.status_code == 200, response.text


RULE = {"name": "errors", "metric": "error_rate", "threshold": 0.1,
        "channel": "slack", "destination": "https://hooks.slack.com/services/T/B/x"}


async def test_viewer_cannot_change_things(client, viewer):
    response = await client.post("/v1/alerts", json=RULE, headers=viewer.headers)
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "insufficient_role"


async def test_developer_adds_an_alert_but_cannot_mint_keys(client, developer):
    response = await client.post("/v1/alerts", json=RULE, headers=developer.headers)
    assert response.status_code == 201, response.text
    response = await client.post("/v1/keys", json={"name": "ci"}, headers=developer.headers)
    assert response.status_code == 403, response.text


async def test_model_slugs_are_unique_across_the_instance(db):
    db.add_all([Model(slug="cells", name="A", task="detection"),
                Model(slug="cells", name="B", task="detection")])
    with pytest.raises(IntegrityError):
        await db.commit()


async def test_unknown_model_is_a_404_not_a_403(client, admin):
    response = await client.post("/v1/models/anything/production/predict", headers=admin.headers)
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "model_not_found"


async def test_workflow_model_node_honours_key_scope(db):
    allowed = Model(slug="model-a", name="A", task="detect")
    hidden = Model(slug="model-b", name="B", task="detect")
    key = ApiKey(name="scoped", prefix="ap_test", key_hash="x", scopes=["model-a"])
    db.add_all([allowed, hidden, key])
    await db.commit()

    graph = {
        "nodes": [{"id": "in", "data": {"type": "input"}},
                  {"id": "m", "data": {"type": "model", "model": "model-b"}}],
        "edges": [{"source": "in", "target": "m"}],
    }
    engine = WorkflowEngine(db, request_id="r", api_key=key)
    result = await engine.run(graph, [InputImage(data=b"x", name="a.jpg", source="test")])
    assert result.status == "error"
    assert result.error_code == "key_not_scoped_to_model"
    assert result.failed_node == "m"
