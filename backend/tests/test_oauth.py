"""OAuth for MCP clients: register, approve on the consent page, exchange, refresh, revoke."""

from __future__ import annotations

import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlsplit

import pytest

from app.models import AuditLog, PersonalAccessToken
from app.settings import settings

from .conftest import bearer, make_pat

REDIRECT = "http://127.0.0.1:33418/callback"


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge.rstrip(b"=").decode()


async def register(client, **body) -> dict:
    response = await client.post("/v1/oauth/register", json={
        "client_name": "Claude Code", "redirect_uris": [REDIRECT], **body})
    assert response.status_code == 201, response.text
    return response.json()


async def approve(client, person, client_id: str, challenge: str, **extra) -> dict:
    params = {"client_id": client_id, "redirect_uri": REDIRECT, "response_type": "code",
              "code_challenge": challenge, "code_challenge_method": "S256", "state": "xyz",
              **extra}
    response = await client.post("/v1/oauth/authorize", json={**params, "approve": True},
                                 headers=person.headers)
    assert response.status_code == 200, response.text
    query = parse_qs(urlsplit(response.json()["redirect_to"]).query)
    return {k: v[0] for k, v in query.items()}


async def exchange(client, client_id: str, code: str, verifier: str):
    return await client.post("/v1/oauth/token", data={
        "grant_type": "authorization_code", "client_id": client_id, "code": code,
        "redirect_uri": REDIRECT, "code_verifier": verifier})


async def connect(client, person) -> tuple[dict, dict]:
    registered = await register(client)
    verifier, challenge = pkce()
    answer = await approve(client, person, registered["client_id"], challenge)
    response = await exchange(client, registered["client_id"], answer["code"], verifier)
    assert response.status_code == 200, response.text
    return registered, response.json()


async def test_metadata_points_at_this_instance(client):
    meta = (await client.get("/.well-known/oauth-authorization-server")).json()
    base = settings.public_url.rstrip("/")
    assert meta["issuer"] == base
    assert meta["authorization_endpoint"] == f"{base}/oauth/authorize"
    assert meta["token_endpoint"] == f"{base}/v1/oauth/token"
    assert meta["registration_endpoint"] == f"{base}/v1/oauth/register"
    assert meta["code_challenge_methods_supported"] == ["S256"]


@pytest.mark.parametrize("uri", [
    "http://evil.example/callback", "javascript:alert(1)", "https://x.example/cb#frag",
    "data:text/html,hi", "",
])
async def test_registration_refuses_unsafe_redirects(client, uri):
    response = await client.post("/v1/oauth/register", json={"redirect_uris": [uri]})
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_redirect_uri"


async def test_registration_accepts_https_loopback_and_app_schemes(client):
    registered = await register(client, redirect_uris=[
        "https://claude.ai/api/mcp/auth_callback", "http://localhost:6274/cb",
        "cursor://anysphere.cursor-retrieval/oauth/callback"])
    assert registered["client_id"].startswith("apc_")
    assert registered["token_endpoint_auth_method"] == "none"


async def test_consent_page_describes_the_client(client, admin):
    registered = await register(client)
    _, challenge = pkce()
    response = await client.get("/v1/oauth/authorize", headers=admin.headers, params={
        "client_id": registered["client_id"], "redirect_uri": REDIRECT,
        "response_type": "code", "code_challenge": challenge, "code_challenge_method": "S256"})
    assert response.status_code == 200, response.text
    view = response.json()
    assert view["client_name"] == "Claude Code"
    assert view["redirect_host"] == "127.0.0.1:33418"
    assert view["user_email"] == "admin@test"


async def test_full_flow_yields_a_token_that_is_the_user(client, admin, db):
    _, tokens = await connect(client, admin)
    assert tokens["access_token"].startswith("ap_pat_")
    assert tokens["refresh_token"].startswith("ap_rt_")
    assert tokens["token_type"] == "Bearer" and tokens["expires_in"] == 3600

    me = await client.get("/v1/auth/me", headers=bearer(tokens["access_token"]))
    assert me.status_code == 200 and me.json()["email"] == "admin@test"

    listed = (await client.get("/v1/auth/tokens", headers=admin.headers)).json()
    assert [(t["name"], t["oauth"]) for t in listed] == [("Claude Code", True)]
    actions = {row.action for row in (await db.execute(
        AuditLog.__table__.select())).fetchall()}
    assert {"oauth.authorize", "token.create"} <= actions


async def test_code_is_single_use_and_needs_the_verifier(client, admin):
    registered = await register(client)
    verifier, challenge = pkce()
    answer = await approve(client, admin, registered["client_id"], challenge)

    wrong = await exchange(client, registered["client_id"], answer["code"], pkce()[0])
    assert wrong.status_code == 400 and wrong.json()["error"] == "invalid_grant"

    ok = await exchange(client, registered["client_id"], answer["code"], verifier)
    assert ok.status_code == 200
    again = await exchange(client, registered["client_id"], answer["code"], verifier)
    assert again.status_code == 400 and again.json()["error"] == "invalid_grant"


async def test_another_client_cannot_spend_the_code(client, admin):
    registered = await register(client)
    other = await register(client, client_name="Other")
    verifier, challenge = pkce()
    answer = await approve(client, admin, registered["client_id"], challenge)
    response = await exchange(client, other["client_id"], answer["code"], verifier)
    assert response.status_code == 400 and response.json()["error"] == "invalid_grant"


async def test_refresh_rotates_both_secrets(client, admin):
    registered, first = await connect(client, admin)
    response = await client.post("/v1/oauth/token", data={
        "grant_type": "refresh_token", "client_id": registered["client_id"],
        "refresh_token": first["refresh_token"]})
    assert response.status_code == 200, response.text
    second = response.json()
    assert second["access_token"] != first["access_token"]
    assert second["refresh_token"] != first["refresh_token"]

    old = await client.get("/v1/auth/me", headers=bearer(first["access_token"]))
    assert old.status_code == 401
    new = await client.get("/v1/auth/me", headers=bearer(second["access_token"]))
    assert new.status_code == 200

    replay = await client.post("/v1/oauth/token", data={
        "grant_type": "refresh_token", "client_id": registered["client_id"],
        "refresh_token": first["refresh_token"]})
    assert replay.status_code == 400 and replay.json()["error"] == "invalid_grant"


async def test_revoking_on_the_tokens_page_ends_the_grant(client, admin, db):
    registered, tokens = await connect(client, admin)
    row = (await db.execute(
        PersonalAccessToken.__table__.select())).fetchone()
    revoked = await client.post(f"/v1/auth/tokens/{row.id}/revoke", headers=admin.headers)
    assert revoked.status_code == 200

    assert (await client.get("/v1/auth/me",
                             headers=bearer(tokens["access_token"]))).status_code == 401
    refresh = await client.post("/v1/oauth/token", data={
        "grant_type": "refresh_token", "client_id": registered["client_id"],
        "refresh_token": tokens["refresh_token"]})
    assert refresh.status_code == 400 and refresh.json()["error"] == "invalid_grant"


async def test_declining_sends_access_denied(client, admin):
    registered = await register(client)
    _, challenge = pkce()
    response = await client.post("/v1/oauth/authorize", headers=admin.headers, json={
        "client_id": registered["client_id"], "redirect_uri": REDIRECT,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": "s1",
        "approve": False})
    query = parse_qs(urlsplit(response.json()["redirect_to"]).query)
    assert query["error"] == ["access_denied"] and query["state"] == ["s1"]
    assert "code" not in query


async def test_consent_refuses_unregistered_redirects_and_missing_pkce(client, admin):
    registered = await register(client)
    _, challenge = pkce()
    base = {"client_id": registered["client_id"], "code_challenge": challenge,
            "code_challenge_method": "S256", "approve": True}
    elsewhere = await client.post("/v1/oauth/authorize", headers=admin.headers,
                                  json={**base, "redirect_uri": "https://evil.example/cb"})
    assert elsewhere.status_code == 400
    plain = await client.post("/v1/oauth/authorize", headers=admin.headers, json={
        **base, "redirect_uri": REDIRECT, "code_challenge_method": "plain"})
    assert plain.status_code == 400


async def test_loopback_redirect_may_change_port(client, admin):
    registered = await register(client)
    _, challenge = pkce()
    response = await client.post("/v1/oauth/authorize", headers=admin.headers, json={
        "client_id": registered["client_id"], "redirect_uri": "http://127.0.0.1:50001/callback",
        "code_challenge": challenge, "code_challenge_method": "S256", "approve": True})
    assert response.status_code == 200
    assert response.json()["redirect_to"].startswith("http://127.0.0.1:50001/callback?code=")


async def test_a_token_cannot_approve_clients(client, admin):
    pat = await make_pat(client, admin.headers)
    registered = await register(client)
    _, challenge = pkce()
    response = await client.post("/v1/oauth/authorize", headers=bearer(pat["token"]), json={
        "client_id": registered["client_id"], "redirect_uri": REDIRECT,
        "code_challenge": challenge, "code_challenge_method": "S256", "approve": True})
    assert response.status_code == 403


async def test_wrong_resource_is_refused(client, admin):
    registered = await register(client)
    _, challenge = pkce()
    response = await client.post("/v1/oauth/authorize", headers=admin.headers, json={
        "client_id": registered["client_id"], "redirect_uri": REDIRECT,
        "code_challenge": challenge, "code_challenge_method": "S256",
        "resource": "https://elsewhere.example/mcp", "approve": True})
    assert response.status_code == 400
    ok = await approve(client, admin, registered["client_id"], challenge,
                       resource=f"{settings.public_url.rstrip('/')}/mcp")
    assert "code" in ok
