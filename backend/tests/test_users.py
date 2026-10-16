"""R7.2 - an admin manages who can use the instance, and with which role."""

from __future__ import annotations

import sqlalchemy as sa

from app.models import AuditLog, User


async def test_admin_invites_changes_role_and_deactivates(client, admin, db):
    invited = await client.put("/v1/users", json={"email": "New@Test", "role": "developer"},
                               headers=admin.headers)
    assert invited.status_code == 200, invited.text
    body = invited.json()
    assert body["email"] == "new@test" and body["role"] == "developer" and body["is_active"]

    promoted = await client.patch(f"/v1/users/{body['id']}", json={"role": "admin"},
                                  headers=admin.headers)
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["role"] == "admin"

    again = await client.put("/v1/users", json={"email": "new@test", "role": "viewer"},
                             headers=admin.headers)
    assert again.json()["id"] == body["id"] and again.json()["role"] == "viewer"

    gone = await client.delete(f"/v1/users/{body['id']}", headers=admin.headers)
    assert gone.status_code == 204, gone.text
    user = await db.get(User, body["id"])
    await db.refresh(user)
    assert user.is_active is False

    listed = (await client.get("/v1/users", headers=admin.headers)).json()
    assert {u["email"] for u in listed} == {"admin@test", "new@test"}

    actions = (await db.execute(
        sa.select(AuditLog.action).where(AuditLog.target_id == body["id"])
        .order_by(AuditLog.created_at)
    )).scalars().all()
    assert actions == ["user.invite", "user.role_change", "user.role_change", "user.deactivate"]


async def test_role_change_applies_on_the_next_request(client, admin, viewer):
    """The session token is not re-issued; the role is read from the user row."""
    rule = {"name": "errors", "metric": "error_rate", "threshold": 0.1,
        "channel": "slack", "destination": "https://hooks.slack.com/services/T/B/x"}
    denied = await client.post("/v1/alerts", json=rule, headers=viewer.headers)
    assert denied.status_code == 403
    await client.patch(f"/v1/users/{viewer.user.id}", json={"role": "developer"},
                       headers=admin.headers)
    allowed = await client.post("/v1/alerts", json=rule, headers=viewer.headers)
    assert allowed.status_code == 201, allowed.text


async def test_non_admins_cannot_manage_users(client, developer, viewer):
    for person in (developer, viewer):
        assert (await client.get("/v1/users", headers=person.headers)).status_code == 403
        response = await client.put("/v1/users", json={"email": "x@test", "role": "admin"},
                                    headers=person.headers)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "insufficient_role"


async def test_admin_cannot_lock_themselves_out(client, admin):
    demote = await client.patch(f"/v1/users/{admin.user.id}", json={"role": "developer"},
                                headers=admin.headers)
    assert demote.status_code == 409, demote.text
    deactivate = await client.delete(f"/v1/users/{admin.user.id}", headers=admin.headers)
    assert deactivate.status_code == 409, deactivate.text
    me = await client.get("/v1/auth/me", headers=admin.headers)
    assert me.json()["role"] == "admin"


async def test_deactivated_user_is_signed_out(client, admin, viewer):
    await client.delete(f"/v1/users/{viewer.user.id}", headers=admin.headers)
    response = await client.get("/v1/auth/me", headers=viewer.headers)
    assert response.status_code == 401
