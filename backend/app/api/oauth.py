"""OAuth 2.1 for MCP clients: Aperture is the authorization server for its own /mcp.

    MCP client                      Aperture
    ----------                      --------
    POST /mcp (no token)        ->  401, WWW-Authenticate: resource_metadata=...
    GET  /.well-known/oauth-protected-resource/mcp    (the MCP server answers)
    GET  /.well-known/oauth-authorization-server      (this module)
    POST /v1/oauth/register     ->  a client_id (RFC 7591, open, public clients)
    browser: /oauth/authorize   ->  the console's consent page, signed in as you
    POST /v1/oauth/token        ->  access token (1 h) + refresh token (30 days)

Registration is open because MCP clients register themselves; it grants
nothing on its own. Every token traces back to a signed-in person who approved
that client on the consent page, for one of the redirect URIs it registered,
with PKCE (S256) binding the code to the client that asked.

The access token is a personal access token. It is the approving user with
their roles today, the API and the MCP server accept it without a new code
path, and the grant shows on that person's tokens page, where revoking it ends
the access and the refresh token together. A refresh replaces both secrets in
place, so a leaked refresh token works once at most before the real client's
next refresh fails loudly.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode, urlsplit

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit
from app.db import get_session
from app.deps import Principal, current_user
from app.errors import ApiError
from app.models import OAuthClient, OAuthCode, PersonalAccessToken, User
from app.security import aware_utc, generate_pat, hash_api_secret
from app.settings import settings

router = APIRouter(prefix="/oauth", tags=["oauth"])
well_known = APIRouter(tags=["oauth"])

ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(days=30)
CODE_TTL = timedelta(minutes=10)
REFRESH_PREFIX = "ap_rt_"
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}
# schemes that could run or read something in the browser instead of handing a code to an app
FORBIDDEN_SCHEMES = {"javascript", "data", "file", "vbscript", "about", "blob", "filesystem"}
MAX_REDIRECT_URIS = 10
NO_STORE = {"cache-control": "no-store", "pragma": "no-cache"}


def issuer() -> str:
    return settings.public_url.rstrip("/")


def resources() -> set[str]:
    """What a client may name in `resource` (RFC 8707): this instance or its /mcp."""
    return {issuer(), f"{issuer()}/mcp"}


# ------------------------------------------------------------------ metadata


@well_known.get("/.well-known/oauth-authorization-server", include_in_schema=False)
async def authorization_server_metadata() -> JSONResponse:
    """RFC 8414. The authorize endpoint is a console page, the rest are API routes."""
    base = issuer()
    return JSONResponse({
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/v1/oauth/token",
        "registration_endpoint": f"{base}/v1/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "authorization_response_iss_parameter_supported": True,
    })


# -------------------------------------------------------------- registration


def _oauth_error(error: str, description: str, status: int = 400) -> JSONResponse:
    """The error shape OAuth clients parse (RFC 6749 §5.2), not Aperture's own."""
    return JSONResponse({"error": error, "error_description": description}, status_code=status,
                        headers=NO_STORE)


def redirect_uri_allowed(uri: str) -> bool:
    """https anywhere, http only to this machine, or an app's own scheme (RFC 8252)."""
    if not uri or len(uri) > 2048:
        return False
    parts = urlsplit(uri)
    scheme = parts.scheme.lower()
    if parts.fragment or not scheme:
        return False
    if scheme == "https":
        return bool(parts.hostname)
    if scheme == "http":
        return (parts.hostname or "") in LOOPBACK_HOSTS
    return scheme not in FORBIDDEN_SCHEMES


@router.post("/register", include_in_schema=False)
async def register(request: Request, session: AsyncSession = Depends(get_session)) -> JSONResponse:
    """RFC 7591 dynamic client registration. Every client is public: no secret."""
    try:
        body = await request.json()
    except ValueError:
        return _oauth_error("invalid_client_metadata", "the body must be JSON")
    if not isinstance(body, dict):
        return _oauth_error("invalid_client_metadata", "the body must be a JSON object")
    uris = body.get("redirect_uris")
    if not isinstance(uris, list) or not uris or len(uris) > MAX_REDIRECT_URIS:
        return _oauth_error("invalid_redirect_uri",
                            f"redirect_uris must list 1 to {MAX_REDIRECT_URIS} URIs")
    for uri in uris:
        if not isinstance(uri, str) or not redirect_uri_allowed(uri):
            return _oauth_error("invalid_redirect_uri", f"redirect URI not allowed: {uri!r}")
    name = str(body.get("client_name") or "").strip()[:160] or "MCP client"
    client_uri = body.get("client_uri")
    if not (isinstance(client_uri, str) and client_uri.startswith("https://")
            and len(client_uri) <= 512):
        client_uri = None

    client = OAuthClient(id="apc_" + secrets.token_urlsafe(16), client_name=name,
                         redirect_uris=list(uris), client_uri=client_uri)
    session.add(client)
    await session.commit()
    await session.refresh(client)
    return JSONResponse({
        "client_id": client.id,
        "client_id_issued_at": int(aware_utc(client.created_at).timestamp()),
        "client_name": client.client_name,
        "client_uri": client.client_uri,
        "redirect_uris": client.redirect_uris,
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
    }, status_code=201, headers=NO_STORE)


# ------------------------------------------------------------- authorization


class AuthorizeIn(BaseModel):
    client_id: str
    redirect_uri: str | None = None
    response_type: str = "code"
    code_challenge: str | None = None
    code_challenge_method: str | None = None
    state: str | None = None
    resource: str | None = None
    scope: str | None = None


class AuthorizeDecision(AuthorizeIn):
    approve: bool = Field(description="false sends the client access_denied")


class ClientView(BaseModel):
    client_id: str
    client_name: str
    client_uri: str | None
    redirect_uri: str
    redirect_host: str
    user_email: str


class Redirect(BaseModel):
    redirect_to: str


def _session_only(principal: Principal) -> None:
    if principal.kind != "user" or principal.token is not None:
        # a token approving clients would let a leaked token mint successors
        raise ApiError("forbidden", "sign in with a browser session to approve an application")


def _redirect_matches(registered: list[str], asked: str) -> bool:
    """Exact match, except that a loopback redirect may name any port (RFC 8252 §7.3)."""
    if asked in registered:
        return True
    want = urlsplit(asked)
    if want.scheme != "http" or (want.hostname or "") not in LOOPBACK_HOSTS:
        return False
    for uri in registered:
        have = urlsplit(uri)
        if (have.scheme, have.hostname, have.path, have.query) == (
                want.scheme, want.hostname, want.path, want.query):
            return True
    return False


async def _validate(session: AsyncSession, params: AuthorizeIn) -> tuple[OAuthClient, str]:
    client = await session.get(OAuthClient, params.client_id)
    if client is None:
        raise ApiError("invalid_request", "unknown client_id; the application must register again")
    registered = list(client.redirect_uris or [])
    redirect = params.redirect_uri
    if redirect is None and len(registered) == 1:
        redirect = registered[0]
    if redirect is None or not _redirect_matches(registered, redirect):
        raise ApiError("invalid_request", "redirect_uri is not one this application registered")
    if params.response_type != "code":
        raise ApiError("invalid_request", "only response_type=code is supported")
    if not params.code_challenge or params.code_challenge_method != "S256":
        raise ApiError("invalid_request", "PKCE with code_challenge_method=S256 is required")
    if params.resource and params.resource.rstrip("/") not in resources():
        raise ApiError("invalid_request",
                       f"resource {params.resource!r} is not this instance ({issuer()}/mcp)")
    return client, redirect


def _with_query(uri: str, values: dict[str, str | None]) -> str:
    query = urlencode({k: v for k, v in values.items() if v is not None})
    return f"{uri}{'&' if urlsplit(uri).query else '?'}{query}"


@router.get("/authorize", response_model=ClientView)
async def describe(params: AuthorizeIn = Depends(),
                   principal: Principal = Depends(current_user),
                   session: AsyncSession = Depends(get_session)) -> ClientView:
    """What the consent page shows: who is asking and where the answer goes."""
    _session_only(principal)
    client, redirect = await _validate(session, params)
    parts = urlsplit(redirect)
    return ClientView(client_id=client.id, client_name=client.client_name,
                      client_uri=client.client_uri, redirect_uri=redirect,
                      redirect_host=parts.netloc or f"{parts.scheme}:",
                      user_email=principal.label)


@router.post("/authorize", response_model=Redirect)
async def decide(body: AuthorizeDecision, request: Request,
                 principal: Principal = Depends(current_user),
                 session: AsyncSession = Depends(get_session)) -> Redirect:
    """The person's answer. Returns where the browser goes next; it never redirects itself."""
    _session_only(principal)
    client, redirect = await _validate(session, body)
    if not body.approve:
        return Redirect(redirect_to=_with_query(redirect, {
            "error": "access_denied", "error_description": "the request was declined",
            "state": body.state, "iss": issuer()}))

    code = secrets.token_urlsafe(32)
    session.add(OAuthCode(
        code_hash=hash_api_secret(code), client_id=client.id, user_id=principal.id,
        redirect_uri=redirect, code_challenge=body.code_challenge or "",
        resource=body.resource, expires_at=datetime.now(UTC) + CODE_TTL,
    ))
    await audit.record(session, action="oauth.authorize", actor_id=principal.id,
                       actor_label=principal.label, target_type="oauth_client",
                       target_id=client.id, target_label=client.client_name,
                       payload={"redirect_uri": redirect},
                       client_ip=request.client.host if request.client else None)
    await session.commit()
    return Redirect(redirect_to=_with_query(redirect, {
        "code": code, "state": body.state, "iss": issuer()}))


# --------------------------------------------------------------------- token


def _pkce_ok(verifier: str, challenge: str) -> bool:
    if not 43 <= len(verifier) <= 128:
        return False
    digest = hashlib.sha256(verifier.encode()).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return hmac.compare_digest(expected, challenge)


def _issue(row: PersonalAccessToken) -> dict:
    """Fresh secrets for a grant row: a new access token and a new refresh token."""
    now = datetime.now(UTC)
    access, prefix, access_hash = generate_pat()
    refresh = REFRESH_PREFIX + secrets.token_urlsafe(32)
    row.prefix, row.token_hash = prefix, access_hash
    row.expires_at = now + ACCESS_TTL
    row.refresh_hash = hash_api_secret(refresh)
    row.refresh_expires_at = now + REFRESH_TTL
    return {"access_token": access, "token_type": "Bearer",
            "expires_in": int(ACCESS_TTL.total_seconds()), "refresh_token": refresh}


@router.post("/token", include_in_schema=False)
async def token(request: Request, session: AsyncSession = Depends(get_session)) -> JSONResponse:
    form = await request.form()
    grant = form.get("grant_type")
    client_id = str(form.get("client_id") or "")
    client = await session.get(OAuthClient, client_id) if client_id else None
    if client is None:
        return _oauth_error("invalid_client", "unknown client_id", status=401)

    if grant == "authorization_code":
        return await _exchange_code(session, request, client, form)
    if grant == "refresh_token":
        return await _refresh(session, client, str(form.get("refresh_token") or ""))
    return _oauth_error("unsupported_grant_type",
                        "grant_type must be authorization_code or refresh_token")


async def _exchange_code(session: AsyncSession, request: Request, client: OAuthClient,
                         form) -> JSONResponse:
    raw = str(form.get("code") or "")
    code = await session.get(OAuthCode, hash_api_secret(raw)) if raw else None
    now = datetime.now(UTC)
    if (code is None or code.client_id != client.id or code.used_at is not None
            or aware_utc(code.expires_at) < now):
        return _oauth_error("invalid_grant", "the code is unknown, expired or already used")
    if str(form.get("redirect_uri") or code.redirect_uri) != code.redirect_uri:
        return _oauth_error("invalid_grant", "redirect_uri does not match the authorization")
    if not _pkce_ok(str(form.get("code_verifier") or ""), code.code_challenge):
        return _oauth_error("invalid_grant", "code_verifier does not match the code_challenge")
    resource = form.get("resource")
    if resource and str(resource).rstrip("/") not in resources():
        return _oauth_error("invalid_target", f"resource is not this instance ({issuer()}/mcp)")
    code.used_at = now

    user = await session.get(User, code.user_id)
    if user is None or not user.is_active:
        await session.commit()
        return _oauth_error("invalid_grant", "the approving account is no longer active")

    row = PersonalAccessToken(user_id=user.id, name=client.client_name,
                              oauth_client_id=client.id, prefix="", token_hash="")
    issued = _issue(row)
    session.add(row)
    await session.flush()
    await audit.record(session, action="token.create", actor_id=user.id,
                       actor_label=user.email, target_type="token", target_id=row.id,
                       target_label=row.name,
                       payload={"prefix": row.prefix, "oauth_client_id": client.id},
                       client_ip=request.client.host if request.client else None)
    await session.commit()
    return JSONResponse(issued, headers=NO_STORE)


async def _refresh(session: AsyncSession, client: OAuthClient, raw: str) -> JSONResponse:
    if not raw.startswith(REFRESH_PREFIX):
        return _oauth_error("invalid_grant", "malformed refresh token")
    found = (await session.execute(
        sa.select(PersonalAccessToken, User)
        .join(User, User.id == PersonalAccessToken.user_id)
        .where(PersonalAccessToken.refresh_hash == hash_api_secret(raw))
    )).first()
    if found is None:
        return _oauth_error("invalid_grant", "refresh token not recognised")
    row, user = found
    if row.oauth_client_id != client.id:
        return _oauth_error("invalid_grant", "this refresh token belongs to another client")
    if row.revoked_at is not None:
        return _oauth_error("invalid_grant", "access was revoked on the tokens page")
    if aware_utc(row.refresh_expires_at) < datetime.now(UTC):
        return _oauth_error("invalid_grant", "the refresh token has expired; sign in again")
    if not user.is_active:
        return _oauth_error("invalid_grant", "the approving account is no longer active")
    issued = _issue(row)
    await session.commit()
    return JSONResponse(issued, headers=NO_STORE)
