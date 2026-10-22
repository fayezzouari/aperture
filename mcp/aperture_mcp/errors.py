"""Every failure the agent sees is one line: the stable error code, the message,
the request id to quote back to whoever runs Aperture, and - where it helps -
what to do about it. The token never appears in any of it."""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import httpx
from mcp.server.mcpserver.exceptions import ToolError

from .client import ApertureError
from .settings import Settings

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])

WORKSPACE_HINT = ("set APERTURE_WORKSPACE or pass workspace=<slug>; "
                  "list_workspaces shows the slugs")
REMOTE_WORKSPACE_HINT = ("send an X-Aperture-Workspace header or pass workspace=<slug>; "
                         "list_workspaces shows the slugs")

TOKEN_CODES = {"unauthenticated", "token_invalid", "token_expired", "token_revoked",
               "api_key_invalid", "api_key_expired", "api_key_revoked"}


def hint_for(error: ApertureError, settings: Settings) -> str | None:
    code = error.code
    if code in TOKEN_CODES:
        if settings.remote:
            return (f"the bearer token was rejected - create a personal access token at "
                    f"{settings.console_url}/account/tokens and put it in the "
                    f"Authorization header of this MCP server's config")
        return (f"APERTURE_TOKEN was rejected - create a personal access token at "
                f"{settings.console_url}/account/tokens and restart the MCP server")
    if code == "forbidden" and settings.mode == "api_key":
        return "an API key only reaches predict and run_workflow; use a personal access token"
    if code == "insufficient_role":
        return "ask a workspace admin to raise your role"
    if code == "model_not_deployed":
        return "deploy the version, or pass ondemand=true to start one replica (developer role)"
    if code in ("model_not_found", "no_workspace") or (
            code == "not_found" and "workspace" in error.message):
        return REMOTE_WORKSPACE_HINT if settings.remote else WORKSPACE_HINT
    if code in ("rate_limited", "queue_full", "no_replica_available"):
        wait = error.details.get("retry_after_seconds")
        return f"retry in {wait} s" if wait else "retry shortly"
    return None


def to_tool_error(exc: Exception, settings: Settings) -> ToolError:
    if isinstance(exc, ApertureError):
        text = f"{exc.code}: {exc.message}"
        if exc.request_id:
            text += f" (request {exc.request_id})"
        hint = hint_for(exc, settings)
        return ToolError(f"{text}. {hint}." if hint else text)
    if isinstance(exc, httpx.TimeoutException):
        return ToolError(f"timeout: Aperture at {settings.console_url} did not answer in time")
    if isinstance(exc, httpx.HTTPError):
        if settings.remote:
            return ToolError(f"unreachable: this MCP server cannot reach Aperture's API "
                             f"({type(exc).__name__}); tell whoever runs {settings.console_url}")
        return ToolError(f"unreachable: cannot reach APERTURE_URL={settings.url} ({exc})")
    if isinstance(exc, ValueError):
        return ToolError(f"invalid_request: {exc}")
    return ToolError(f"internal_error: {type(exc).__name__}: {exc}")


def guarded(settings: Settings) -> Callable[[F], F]:
    """Decorator for tool bodies: turns transport and API failures into tool errors."""

    def decorate(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await func(*args, **kwargs)
            except ToolError:
                raise
            except Exception as exc:  # every failure becomes one tool error
                raise to_tool_error(exc, settings) from exc

        return wrapper  # type: ignore[return-value]

    return decorate
