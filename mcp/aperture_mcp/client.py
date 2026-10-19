"""A small async client for Aperture's /v1 API.

Deliberately not the vendored `sdk/python` client: that one is synchronous,
authenticates with an API key and only knows the inference routes. This one
carries a Bearer token, speaks to the control plane and never touches the
database or Redis - the API stays the single place where roles, rate limits and
the audit log are enforced.
"""

from __future__ import annotations

from typing import Any

import httpx

from .settings import Settings

WORKSPACE_HEADER = "x-aperture-workspace"


class ApertureError(RuntimeError):
    """Aperture's error contract: {"error": {"code", "message", "request_id", "details"}}."""

    def __init__(self, status: int, code: str, message: str, request_id: str | None = None,
                 details: dict | None = None):
        self.status = status
        self.code = code
        self.message = message
        self.request_id = request_id
        self.details = details or {}
        super().__init__(f"{code}: {message}")


class ApertureClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self._http = httpx.AsyncClient(
            base_url=settings.url,
            headers={"authorization": f"Bearer {settings.token}",
                     "user-agent": "aperture-mcp"},
            timeout=settings.timeout_s,
            transport=transport,
        )
        # Presigned object-store URLs must travel without our Authorization header:
        # MinIO refuses a request that carries two credentials.
        self._plain = httpx.AsyncClient(timeout=settings.timeout_s, transport=transport,
                                        follow_redirects=True)

    async def aclose(self) -> None:
        await self._http.aclose()
        await self._plain.aclose()

    # ------------------------------------------------------------------ calls

    def _headers(self, workspace: str | None) -> dict[str, str]:
        slug = workspace or self.settings.workspace
        return {WORKSPACE_HEADER: slug} if slug else {}

    async def get(self, path: str, *, params: dict[str, Any] | None = None,
                  workspace: str | None = None) -> Any:
        response = await self._http.get(path, params=_clean(params),
                                        headers=self._headers(workspace))
        return self._body(response)

    async def post_json(self, path: str, body: dict[str, Any], *,
                        params: dict[str, Any] | None = None, workspace: str | None = None,
                        timeout: float | None = None) -> Any:
        response = await self._http.post(path, json=body, params=_clean(params),
                                         headers=self._headers(workspace),
                                         timeout=timeout or self.settings.timeout_s)
        return self._body(response)

    async def fetch_bytes(self, url: str) -> bytes:
        """Download a presigned capture or overlay. No Aperture credentials attached."""
        response = await self._plain.get(url)
        if response.status_code >= 400:
            raise ApertureError(response.status_code, "capture_unavailable",
                                f"could not fetch {url.split('?')[0]}: HTTP {response.status_code}")
        return response.content

    async def openapi(self) -> dict:
        response = await self._http.get("/api/openapi.json")
        return self._body(response)

    # ---------------------------------------------------------------- errors

    @staticmethod
    def _body(response: httpx.Response) -> Any:
        if response.status_code < 400:
            if not response.content:
                return None
            return response.json()
        try:
            error = response.json().get("error") or {}
        except ValueError:
            error = {}
        raise ApertureError(
            response.status_code,
            error.get("code") or f"http_{response.status_code}",
            error.get("message") or response.text[:200] or response.reason_phrase,
            error.get("request_id") or response.headers.get("x-request-id"),
            error.get("details"),
        )


def _clean(params: dict[str, Any] | None) -> dict[str, Any] | None:
    """Drop None so an unset optional never turns into `?tag=None`."""
    if not params:
        return None
    out = {}
    for key, value in params.items():
        if value is None:
            continue
        out[key] = str(value).lower() if isinstance(value, bool) else value
    return out or None
