"""R8.1 - personal access tokens behave like the user who issued them, until revoked."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from app.models import AuditLog, PersonalAccessToken, User

from .conftest import bearer, make_pat


async def test_create_shows_token_once_and_stores_only_a_hash(client, admin, db):
    created = await make_pat(client, admin.headers, name="laptop")
    assert created["token"].startswith("ap_pat_") and "." in created["token"]
    assert created["prefix"] == created["token"].split(".")[0]
    assert "token_hash" not in created
    assert created["expires_at"] is not None  # default 90 days

    row = await db.get(PersonalAccessToken, created["id"])
    assert row.token_hash != created["token"]
    assert created["token"].split(".", 1)[1] not in row.token_hash

    listed = (await client.get("/v1/auth/tokens", headers=admin.headers)).json()
    assert [t["id"] for t in listed] == [created["id"]]
    assert "token" not in listed[0]


async def test_never_expires_when_asked(client, admin):
    created = await make_pat(client, admin.headers, name="forever", expires_in_days=None)
    assert created["expires_at"] is None


async def test_token_acts_as_its_owner(client, admin):
    created = await make_pat(client, admin.headers)
    me = await client.get("/v1/auth/me", headers=bearer(created["token"]))
    assert me.status_code == 200, me.text
    assert me.json()["email"] == "admin@test"
    assert me.json()["roles"]  # the owner's memberships travel with the token

    models = await client.get("/v1/models", headers=bearer(created["token"]))
    assert models.status_code == 200, models.text


async def test_token_only_lists_its_owners_tokens(client, admin, viewer):
    mine = await make_pat(client, admin.headers, name="mine")
    await make_pat(client, viewer.headers, name="theirs")
    seen = (await client.get("/v1/auth/tokens", headers=bearer(mine["token"]))).json()
    assert {t["name"] for t in seen} == {"mine"}


async def test_wrong_secret_is_rejected(client, admin):
    created = await make_pat(client, admin.headers)
    forged = created["prefix"] + ".not-the-secret"
    response = await client.get("/v1/auth/me", headers=bearer(forged))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "token_invalid"

    response = await client.get("/v1/auth/me", headers=bearer("ap_pat_nodot"))
    assert response.json()["error"]["code"] == "token_invalid"


async def test_revoke_stops_it_immediately(client, admin):
    created = await make_pat(client, admin.headers)
    revoked = await client.post(f"/v1/auth/tokens/{created['id']}/revoke", headers=admin.headers)
    assert revoked.status_code == 200 and revoked.json()["revoked_at"] is not None

    response = await client.get("/v1/auth/me", headers=bearer(created["token"]))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "token_revoked"

    hidden = (await client.get("/v1/auth/tokens", headers=admin.headers)).json()
    assert created["id"] not in {t["id"] for t in hidden}
    shown = (await client.get("/v1/auth/tokens?include_revoked=true",
                              headers=admin.headers)).json()
    assert created["id"] in {t["id"] for t in shown}


async def test_a_token_may_revoke_itself(client, admin):
    created = await make_pat(client, admin.headers)
    response = await client.post(f"/v1/auth/tokens/{created['id']}/revoke",
                                 headers=bearer(created["token"]))
    assert response.status_code == 200
    assert response.json()["revoked_at"] is not None


async def test_someone_else_cannot_revoke_it(client, admin, viewer):
    created = await make_pat(client, admin.headers)
    response = await client.post(f"/v1/auth/tokens/{created['id']}/revoke",
                                 headers=viewer.headers)
    assert response.status_code == 404  # indistinguishable from "no such token"
    still = await client.get("/v1/auth/me", headers=bearer(created["token"]))
    assert still.status_code == 200


async def test_expired_token_is_rejected(client, admin, db):
    created = await make_pat(client, admin.headers)
    await db.execute(sa.update(PersonalAccessToken)
                     .where(PersonalAccessToken.id == created["id"])
                     .values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    await db.commit()
    response = await client.get("/v1/auth/me", headers=bearer(created["token"]))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "token_expired"


async def test_deactivated_owner_takes_the_token_down(client, admin, db):
    created = await make_pat(client, admin.headers)
    await db.execute(sa.update(User).where(User.id == admin.user.id).values(is_active=False))
    await db.commit()
    response = await client.get("/v1/auth/me", headers=bearer(created["token"]))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


async def test_a_token_cannot_mint_tokens(client, admin):
    created = await make_pat(client, admin.headers)
    response = await client.post("/v1/auth/tokens", json={"name": "child"},
                                 headers=bearer(created["token"]))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


async def test_token_is_not_an_api_key(client, admin, project):
    """A PAT in x-api-key is a malformed API key; on the inference path a Bearer PAT is the user."""
    created = await make_pat(client, admin.headers)
    response = await client.post("/v1/models/nothing/production/predict",
                                 headers={"x-api-key": created["token"]}, json={"images": []})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "api_key_invalid"

    response = await client.post("/v1/models/nothing/production/predict",
                                 headers=bearer(created["token"]), json={"images": []})
    # authenticated as the user, then fails on the model - never on the credential
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "model_not_found"


async def test_last_used_is_recorded(client, admin, db):
    created = await make_pat(client, admin.headers)
    assert created["last_used_at"] is None
    await client.get("/v1/auth/me", headers=bearer(created["token"]))
    row = await db.get(PersonalAccessToken, created["id"])
    await db.refresh(row)
    assert row.last_used_at is not None


async def test_audit_trail(client, admin, db):
    created = await make_pat(client, admin.headers, name="audited")
    await client.post(f"/v1/auth/tokens/{created['id']}/revoke", headers=admin.headers)
    rows = (await db.execute(
        sa.select(AuditLog).where(AuditLog.target_id == created["id"])
        .order_by(AuditLog.created_at)
    )).scalars().all()
    assert [r.action for r in rows] == ["token.create", "token.revoke"]
    assert all(r.project_id is None for r in rows)
    assert all(r.actor_label == "admin@test" for r in rows)
    assert rows[0].payload["prefix"] == created["prefix"]


async def test_actions_taken_with_a_token_are_marked_in_the_audit_log(client, admin, db):
    """Same user, same route: the audit row says how they authenticated."""
    created = await make_pat(client, admin.headers)
    response = await client.post("/v1/keys", json={"name": "via token"},
                                 headers=bearer(created["token"]))
    assert response.status_code == 201, response.text
    response = await client.post("/v1/keys", json={"name": "via session"}, headers=admin.headers)
    assert response.status_code == 201, response.text

    rows = {
        r.target_label: r.actor_type for r in (await db.execute(
            sa.select(AuditLog).where(AuditLog.action == "apikey.create")
        )).scalars().all()
    }
    assert rows == {"via token": "user:token", "via session": "user"}
