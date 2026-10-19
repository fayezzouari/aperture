"""R7.1-R7.3 - a caller reaches what its workspace role allows, and an API key only its models."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import ApiKey, Model, WorkspaceMember
from app.services.gateway import InputImage
from app.services.workflow import WorkflowEngine

from .conftest import add_workspace

RULE = {"name": "errors", "metric": "error_rate", "threshold": 0.1,
        "channel": "slack", "destination": "https://hooks.slack.com/services/T/B/x"}
MODEL = {"slug": "cells", "name": "Cells", "task": "detection"}


@pytest.fixture(autouse=True)
def _no_traffic_stats(monkeypatch):
    # model cards use percentile_cont, which SQLite does not have
    async def no_cards(session, *, model_ids):
        return {}

    monkeypatch.setattr("app.api.v1.models_api.model_cards", no_cards)


async def test_viewer_cannot_change_things(client, viewer):
    response = await client.post("/v1/alerts", json=RULE, headers=viewer.headers)
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "insufficient_role"


async def test_developer_adds_an_alert_but_cannot_mint_keys(client, developer):
    response = await client.post("/v1/alerts", json=RULE, headers=developer.headers)
    assert response.status_code == 201, response.text
    response = await client.post("/v1/keys", json={"name": "ci"}, headers=developer.headers)
    assert response.status_code == 403, response.text


async def test_model_slugs_are_unique_per_workspace(db):
    one, two = await add_workspace(db, "one"), await add_workspace(db, "two")
    db.add_all([Model(workspace_id=one.id, slug="cells", name="A", task="detection"),
                Model(workspace_id=two.id, slug="cells", name="B", task="detection")])
    await db.commit()  # the same slug in two workspaces is fine
    db.add(Model(workspace_id=one.id, slug="cells", name="C", task="detection"))
    with pytest.raises(IntegrityError):
        await db.commit()


async def test_unknown_model_is_a_404_not_a_403(client, admin):
    response = await client.post("/v1/models/anything/production/predict", headers=admin.headers)
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "model_not_found"


async def test_models_stay_in_their_workspace(client, admin, developer, db):
    other = await add_workspace(db, "cloud")
    await db.commit()
    created = await client.post("/v1/models", json=MODEL,
                                headers={**admin.headers, "x-aperture-workspace": "cloud"})
    assert created.status_code == 201, created.text
    assert created.json()["workspace_id"] == other.id

    # the developer is only in `main`, which does not have it
    mine = await client.get("/v1/models", headers=developer.headers)
    assert mine.status_code == 200 and mine.json() == []
    hidden = await client.get("/v1/models/cells", headers=developer.headers)
    assert hidden.status_code == 404, hidden.text
    # and naming a workspace you are not in is refused, not quietly ignored
    refused = await client.get("/v1/models",
                               headers={**developer.headers, "x-aperture-workspace": "cloud"})
    assert refused.status_code == 403, refused.text


async def test_the_member_role_decides_inside_a_workspace(client, viewer, db, workspace):
    other = await add_workspace(db, "lab")
    db.add(WorkspaceMember(user_id=viewer.user.id, workspace_id=other.id, role="developer"))
    await db.commit()
    lab = {**viewer.headers, "x-aperture-workspace": "lab"}
    assert (await client.post("/v1/models", json=MODEL, headers=lab)).status_code == 201
    main = await client.post("/v1/models", json=MODEL,
                             headers={**viewer.headers, "x-aperture-workspace": "main"})
    assert main.status_code == 403, main.text


async def test_a_developer_creates_a_workspace_and_lands_in_it(client, developer, viewer):
    created = await client.post("/v1/workspaces", json={"slug": "cloud", "name": "Cloud"},
                                headers=developer.headers)
    assert created.status_code == 201, created.text
    assert created.json()["role"] == "admin"

    me = (await client.get("/v1/auth/me", headers=developer.headers)).json()
    assert me["last_workspace_id"] == created.json()["id"]
    # with no header, requests now act in the workspace switched to last
    made = await client.post("/v1/models", json=MODEL, headers=developer.headers)
    assert made.json()["workspace_id"] == created.json()["id"]

    slugs = [w["slug"] for w in (await client.get("/v1/workspaces",
                                                  headers=developer.headers)).json()]
    assert slugs == ["cloud", "main"]  # by name
    refused = await client.post("/v1/workspaces", json={"slug": "mine", "name": "Mine"},
                                headers=viewer.headers)
    assert refused.status_code == 403, refused.text


async def test_activate_remembers_the_workspace(client, admin, db):
    lab = await add_workspace(db, "lab")
    await db.commit()
    response = await client.post("/v1/workspaces/lab/activate", headers=admin.headers)
    assert response.status_code == 200, response.text
    me = (await client.get("/v1/auth/me", headers=admin.headers)).json()
    assert me["last_workspace_id"] == lab.id


async def test_without_a_workspace_the_answer_says_so(client, db):
    from .conftest import _person

    lonely = await _person(db, "lonely@test", "developer")
    response = await client.get("/v1/models", headers=lonely.headers)
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "no_workspace"
    assert (await client.get("/v1/workspaces", headers=lonely.headers)).json() == []


async def test_members_are_managed_by_the_workspace_admin(client, developer, viewer):
    await client.post("/v1/workspaces", json={"slug": "cloud", "name": "Cloud"},
                      headers=developer.headers)
    added = await client.put("/v1/workspaces/cloud/members",
                             json={"email": "viewer@test", "role": "viewer"},
                             headers=developer.headers)
    assert added.status_code == 200, added.text
    # a viewer there cannot add anyone
    refused = await client.put("/v1/workspaces/cloud/members",
                               json={"email": "viewer@test", "role": "admin"},
                               headers=viewer.headers)
    assert refused.status_code == 403, refused.text
    # and the last admin cannot step down
    last = await client.put("/v1/workspaces/cloud/members",
                            json={"email": "developer@test", "role": "viewer"},
                            headers=developer.headers)
    assert last.status_code == 409, last.text


async def test_a_key_needs_the_workspace_when_a_slug_repeats(client, db):
    from app.deps import get_model_or_404
    from app.errors import ApiError

    one, two = await add_workspace(db, "one"), await add_workspace(db, "two")
    db.add_all([Model(workspace_id=one.id, slug="cells", name="A", task="detection"),
                Model(workspace_id=two.id, slug="cells", name="B", task="detection")])
    await db.commit()
    with pytest.raises(ApiError, match="more than one workspace"):
        await get_model_or_404(db, "cells", None)
    found = await get_model_or_404(db, "cells", two.id)
    assert found.name == "B"


async def test_workflow_model_node_honours_key_scope(db):
    ws = await add_workspace(db)
    allowed = Model(workspace_id=ws.id, slug="model-a", name="A", task="detect")
    hidden = Model(workspace_id=ws.id, slug="model-b", name="B", task="detect")
    key = ApiKey(name="scoped", prefix="ap_test", key_hash="x", scopes=["model-a"])
    db.add_all([allowed, hidden, key])
    await db.commit()

    graph = {
        "nodes": [{"id": "in", "data": {"type": "input"}},
                  {"id": "m", "data": {"type": "model", "model": "model-b"}}],
        "edges": [{"source": "in", "target": "m"}],
    }
    engine = WorkflowEngine(db, request_id="r", api_key=key, workspace_id=ws.id)
    result = await engine.run(graph, [InputImage(data=b"x", name="a.jpg", source="test")])
    assert result.status == "error"
    assert result.error_code == "key_not_scoped_to_model"
    assert result.failed_node == "m"
