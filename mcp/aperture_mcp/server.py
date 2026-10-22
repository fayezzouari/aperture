"""The MCP server: Aperture's /v1 API as tools, over stdio or shared HTTP.

    APERTURE_URL=http://localhost:8080 APERTURE_TOKEN=ap_pat_... aperture-mcp
    APERTURE_URL=http://api:8000 aperture-mcp --http          # see remote.py

Over stdio it runs on the engineer's machine next to the agent, so file paths in
`predict` resolve locally. Nothing is written to stdout except the protocol;
logs go to stderr.
"""

from __future__ import annotations

import logging
import sys

import httpx
from mcp.server import MCPServer

from .client import ApertureClient
from .resources import register_resources
from .settings import ConfigError, Settings, load_remote_settings, load_settings
from .state import AppState
from .tools.act import register_act_tools
from .tools.read import register_read_tools

log = logging.getLogger("aperture.mcp")

INSTRUCTIONS = """\
Aperture serves, versions and observes computer-vision models. Products call
`POST /v1/models/<model>/<tag>/predict`; this server gives you the same registry,
traffic and fleet the console shows, behind the token's own roles.

Vocabulary: a *model* is a slug like `part-detection`; each *version* is immutable
once published and carries its own preprocessing/decoder config; *tags*
(`production`, `staging`) point at a version and resolve per request; a
*deployment* keeps a version resident on runtimes; a *workflow* chains models as
one endpoint. `ref` on predict is a tag or a version number.

Start with `get_overview` for health, `list_models` for what exists, `get_model`
for one model's card. `predict` returns the overlay as an image - look at it.
Models and workflows live in *workspaces*; every tool takes `workspace=<slug>`
to act in another one, and `list_workspaces` shows them.
"""

API_KEY_INSTRUCTIONS = """\
APERTURE_TOKEN is an API key, which reaches only the inference routes: `predict`
and `run_workflow` are available; the registry, metrics and logs are not. A
personal access token (created at <url>/account/tokens) unlocks the rest.
"""


def build_server(settings: Settings, client: ApertureClient | None = None) -> MCPServer:
    state = AppState(settings=settings, client=client or ApertureClient(settings))
    if settings.remote:
        # every caller's token arrives per request; BindCaller trims the list for keys
        from .remote import BindCaller

        mcp = MCPServer("aperture", instructions=INSTRUCTIONS, middleware=[BindCaller()])
        register_read_tools(mcp, state)
        register_act_tools(mcp, state)
        register_resources(mcp, state)
        return mcp
    if settings.mode == "api_key":
        mcp = MCPServer("aperture", instructions=API_KEY_INSTRUCTIONS.replace("<url>", settings.url))
        register_act_tools(mcp, state)
        return mcp
    mcp = MCPServer("aperture", instructions=INSTRUCTIONS)
    register_read_tools(mcp, state)
    register_act_tools(mcp, state)
    register_resources(mcp, state)
    return mcp


def _announce(settings: Settings) -> None:
    """Say who we are on stderr. Never fatal: the first tool call reports auth errors."""
    if settings.mode == "api_key":
        log.warning("APERTURE_TOKEN is an API key: only predict and run_workflow are available")
        return
    if settings.mode == "session":
        log.warning("APERTURE_TOKEN looks like a session token; it will expire - "
                    "prefer a personal access token from %s/account/tokens", settings.url)
    try:
        response = httpx.get(f"{settings.url}/v1/auth/me", timeout=10,
                             headers={"authorization": f"Bearer {settings.token}"})
        if response.status_code == 200:
            me = response.json()
            log.info("aperture-mcp: %s at %s, role %s, workspace %s", me.get("email"),
                     settings.url, me.get("role") or "unknown",
                     settings.workspace or "last used")
        else:
            code = (response.json().get("error") or {}).get("code", response.status_code)
            log.warning("aperture-mcp: %s rejected the token (%s)", settings.url, code)
    except Exception as exc:  # noqa: BLE001 - startup must not depend on the network
        log.warning("aperture-mcp: could not reach %s (%s)", settings.url, exc)


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    remote = "--http" in sys.argv[1:]
    try:
        settings = load_remote_settings() if remote else load_settings()
    except ConfigError as exc:
        print(f"aperture-mcp: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    if remote:
        from .remote import serve

        serve(settings)
        return
    _announce(settings)
    build_server(settings).run(transport="stdio")


if __name__ == "__main__":
    main()
