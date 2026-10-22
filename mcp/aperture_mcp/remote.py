"""The same tools, shared over streamable HTTP.

    APERTURE_URL=http://api:8000 APERTURE_PUBLIC_URL=https://aperture.internal \
      aperture-mcp --http

    claude mcp add --transport http aperture https://aperture.internal/mcp \
      --header "Authorization: Bearer ap_pat_..."

Nobody installs anything: the console's edge proxies /mcp here, and each request
carries its caller's own token, so a call can do exactly what that person can do
in the console and the audit log names them. This process holds no credential
and keeps no session; every request stands alone.

Two things differ from stdio. File paths in `predict` are refused, because the
files this process could read are its own. And an API key sees only `predict`
and `run_workflow` in the tool list, as it would over stdio.

A client that arrives without a token is pointed at OAuth: the 401 names this
server's protected-resource metadata (RFC 9728), which names Aperture itself as
the authorization server. The client registers, sends the person to the
console's consent page, and comes back with a token the API already accepts.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

import httpx
from mcp.server.context import CallNext, ServerRequestContext
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from . import caller
from .client import WORKSPACE_HEADER, ApertureClient
from .settings import Settings, mode_of

log = logging.getLogger("aperture.mcp")

MCP_PATH = "/mcp"
RESOURCE_METADATA_PATHS = ("/.well-known/oauth-protected-resource",
                           "/.well-known/oauth-protected-resource" + MCP_PATH)
INFERENCE_TOOLS = {"predict", "run_workflow"}
# a batch of images arrives as base64 inside one JSON-RPC body
MAX_BODY_BYTES = 48 * 1024 * 1024


def bearer(headers: Headers) -> str | None:
    scheme, _, token = (headers.get("authorization") or "").partition(" ")
    token = token.strip()
    return token if scheme.lower() == "bearer" and token else None


class BindCaller:
    """MCP middleware: the request's token and workspace become the caller for
    everything the handler does, and an API key's tool list is cut to what it
    can reach."""

    async def __call__(self, ctx: ServerRequestContext[Any, Any], call_next: CallNext) -> Any:
        request = ctx.request
        headers = request.headers if request is not None else Headers()
        token = bearer(headers) or ""
        bound = caller.bind(caller.Caller(token=token,
                                          workspace=headers.get(WORKSPACE_HEADER) or None))
        try:
            result = await call_next(ctx)
        finally:
            caller.unbind(bound)
        if ctx.method == "tools/list" and mode_of(token) == "api_key" and isinstance(result, dict):
            result = {**result, "tools": [tool for tool in result.get("tools", [])
                                          if tool.get("name") in INFERENCE_TOOLS]}
        return result


class TokenCheck:
    """Asks the API who a personal access token belongs to, and remembers the
    answer briefly, so a revoked or mistyped token fails at connect time with a
    401 instead of on every tool call. Only a definite rejection refuses; if the
    API cannot be reached the request goes through and the tool reports it."""

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None,
                 ttl_s: float = 60.0):
        self._http = httpx.AsyncClient(base_url=settings.url, timeout=10, transport=transport,
                                       headers={"user-agent": "aperture-mcp"})
        self._ttl = ttl_s
        self._seen: dict[str, tuple[float, dict | None]] = {}

    async def aclose(self) -> None:
        await self._http.aclose()

    async def rejection(self, token: str) -> dict | None:
        """None when the token may proceed, else Aperture's error body."""
        if mode_of(token) != "pat":
            return None  # an API key cannot read /v1/auth/me; the API judges it per call
        key = hashlib.sha256(token.encode()).hexdigest()
        now = time.monotonic()
        hit = self._seen.get(key)
        if hit and now - hit[0] < self._ttl:
            return hit[1]
        try:
            response = await self._http.get("/v1/auth/me",
                                            headers={"authorization": f"Bearer {token}"})
        except httpx.HTTPError as exc:
            log.warning("aperture-mcp: token check could not reach the API (%s)", exc)
            return None
        verdict = None
        if response.status_code == 401:
            try:
                verdict = response.json()
            except ValueError:
                verdict = {"error": {"code": "unauthenticated", "message": "token rejected"}}
        if len(self._seen) > 4096:
            self._seen.clear()
        self._seen[key] = (now, verdict)
        return verdict


class Gate:
    """ASGI in front of the MCP app: health, and no request without a bearer token."""

    def __init__(self, app: ASGIApp, settings: Settings, check: TokenCheck):
        self.app = app
        self.settings = settings
        self.check = check

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if scope["path"] == "/healthz":
            await _reply(send, 200, {"status": "ok"})
            return
        if scope["path"] in RESOURCE_METADATA_PATHS:
            await _reply(send, 200, resource_metadata(self.settings))
            return
        token = bearer(Headers(scope=scope))
        if token is None:
            await _reply(send, 401, _error(
                "unauthenticated",
                "sign in through OAuth, or send Authorization: Bearer <personal access "
                f"token>; create one at {self.settings.console_url}/account/tokens"),
                challenge=self._challenge())
            return
        rejected = await self.check.rejection(token)
        if rejected is not None:
            await _reply(send, 401, rejected, challenge=self._challenge('error="invalid_token"'))
            return
        await self.app(scope, receive, send)

    def _challenge(self, *extra: str) -> str:
        metadata = self.settings.console_url + RESOURCE_METADATA_PATHS[1]
        return ", ".join(['Bearer realm="aperture"', *extra, f'resource_metadata="{metadata}"'])


def resource_metadata(settings: Settings) -> dict:
    """RFC 9728: this MCP endpoint, protected by Aperture's own authorization server."""
    base = settings.console_url
    return {
        "resource": base + MCP_PATH,
        "authorization_servers": [base],
        "bearer_methods_supported": ["header"],
        "resource_name": "Aperture",
        "resource_documentation": f"{base}/account/tokens",
    }


def _error(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


async def _reply(send: Send, status: int, body: dict, challenge: str | None = None) -> None:
    payload = json.dumps(body).encode()
    headers = [(b"content-type", b"application/json"),
               (b"content-length", str(len(payload)).encode())]
    if challenge:
        headers.append((b"www-authenticate", challenge.encode()))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": payload})


def build_app(settings: Settings, client: ApertureClient | None = None,
              check: TokenCheck | None = None) -> ASGIApp:
    from .server import build_server

    server = build_server(settings, client=client)
    # Stateless with plain JSON responses: any replica can answer any request, and
    # nothing here streams - every tool is one call to /v1 and one answer.
    app = server.streamable_http_app(
        streamable_http_path=MCP_PATH, stateless_http=True, json_response=True,
        max_request_body_size=MAX_BODY_BYTES,
        # Host and Origin are the edge proxy's to police; a bearer token is required
        # on every request, which a rebinding page does not have.
        host=settings.host,
    )
    return Gate(app, settings, check or TokenCheck(settings))


def serve(settings: Settings) -> None:
    import uvicorn

    log.info("aperture-mcp: serving %s%s for %s, API at %s", f"{settings.host}:{settings.port}",
             MCP_PATH, settings.console_url, settings.url)
    uvicorn.run(build_app(settings), host=settings.host, port=settings.port,
                log_level="info", access_log=False, proxy_headers=True)
