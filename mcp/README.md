# aperture-mcp

Aperture's API as MCP tools, over stdio or streamable HTTP. Point a coding agent
at an Aperture instance and it can read the registry, the fleet, the traffic and
the audit log, run an image through a model, and *look* at the overlay that came
back.

It is a client of `/v1` and nothing else: no database credentials, no Redis, no
bypass. Everything it can do is what the token owner's roles allow: the role
they hold in each workspace, and their instance role for the rest.

## Run it

Create a personal access token in the console (**account menu → Personal access
tokens**), then:

```bash
APERTURE_URL=http://localhost:8080 \
APERTURE_TOKEN=ap_pat_xxxx.yyyy \
APERTURE_WORKSPACE=cloud \
  uvx --from . aperture-mcp
```

In Claude Code:

```bash
claude mcp add aperture \
  -e APERTURE_URL=http://localhost:8080 \
  -e APERTURE_TOKEN=ap_pat_xxxx.yyyy \
  -e APERTURE_WORKSPACE=cloud \
  -- uvx --from /path/to/aperture/mcp aperture-mcp
```

Cursor takes the same three variables as JSON; the README at the repository
root has that form. For Claude Desktop, use the extension below.

| Variable | |
|---|---|
| `APERTURE_URL` | required, e.g. `http://localhost:8080` |
| `APERTURE_TOKEN` | required. `ap_pat_…` for everything; an `ap_…` API key registers only `predict` and `run_workflow`, because that is all a key can reach |
| `APERTURE_WORKSPACE` | optional default workspace slug; every tool also takes `workspace=`. Unset, a user's calls act in the workspace they opened last in the console. An API key acts in the workspace it belongs to, so leave it unset or set it to that workspace |

## Run it shared, over HTTP

```bash
APERTURE_URL=http://api:8000 APERTURE_PUBLIC_URL=https://aperture.internal \
  aperture-mcp --http
```

This is the `mcp` service in `docker-compose.yml`; the web edge proxies `/mcp`
to it. It holds no token: every request sends its caller's in
`Authorization: Bearer`, plus an optional `X-Aperture-Workspace`, and the
server binds them for that request only.

Callers get that token through OAuth. A request without one gets a 401 whose
`WWW-Authenticate` names `/.well-known/oauth-protected-resource/mcp`, which this
server answers with Aperture's API as the authorization server. Registration,
the consent page and the token endpoint live in the API (`backend/app/api/oauth.py`);
what comes back is a personal access token, so nothing here changes for it. Requests without a token get a 401
before MCP sees them, and a personal access token the API rejects is refused at
connect time (the verdict is cached for 60 s). It runs stateless with JSON
responses, so replicas need no shared state.

```bash
claude mcp add --transport http aperture https://aperture.internal/mcp   # sign in via /mcp
claude mcp add --transport http aperture https://aperture.internal/mcp \
  --header "Authorization: Bearer ap_pat_xxxx.yyyy"                       # or a token
```

What differs from stdio: `predict` and `run_workflow` refuse file paths, since
the only files this process could read are its own; an API key still sees only
those two tools.

| Variable | |
|---|---|
| `APERTURE_URL` | required: the API as this process reaches it, e.g. `http://api:8000` |
| `APERTURE_PUBLIC_URL` | the console's address, used in error hints |
| `APERTURE_MCP_HOST`, `APERTURE_MCP_PORT` | default `0.0.0.0`, `8090` |
| `APERTURE_S3_PUBLIC_ENDPOINT`, `APERTURE_S3_ENDPOINT` | set both to fetch presigned captures from the internal object store endpoint, keeping the public Host the signature was made for |

## Claude Desktop extension

```bash
bundle/build.sh          # -> dist/aperture.mcpb ; `make mcpb` at the root
```

A uv bundle: the package source, `pyproject.toml` and `uv.lock`, with Claude
Desktop installing the locked dependencies, so one file serves every platform.
Installing it asks for the instance URL, a token (stored in the OS keychain) and
an optional workspace, and runs the stdio server. The web image builds the same
file and serves it at `/downloads/aperture.mcpb`.

## Tools

**Registry** — `list_workspaces`, `list_models`, `get_model`, `list_versions`,
`get_version`, `compare_versions`, `list_presets`, `get_preset`.

**Operations** — `get_overview`, `get_metrics`, `get_errors`, `list_runtimes`,
`get_drift`, `list_jobs`, `get_job`.

**The log** — `search_inferences`, `get_inference` (returns the captured frame
and its overlay as images), `search_audit`.

**Workflows** — `list_workflows`, `get_workflow`.

**Running something** — `predict`, `run_workflow`. Both accept local file paths,
`http(s)` URLs, data URLs or bare base64, and return the runtime-rendered
overlay as an image block next to a per-class summary. `ondemand=true` on
`predict` starts one replica for an undeployed version and waits for it, which
needs the developer role.

Everything else is annotated read-only. Failures arrive as one line: the stable
error code, the message, the request id, and a hint where there is one.

## Resources

```
aperture://me
aperture://workspaces
aperture://workspaces/{slug}/models/{ref}
aperture://models/{ref}
aperture://models/{ref}/versions/{n}/config
aperture://presets/{name}
aperture://schema/config
aperture://openapi
```

## Develop

```bash
uv run --extra dev pytest -q          # tools against a fake /v1
uv run --extra dev ruff check aperture_mcp tests
```

The tests drive the real server object in-process (`build_server`) with
`httpx.MockTransport` underneath, so they assert what each tool *sends* as well
as what it returns. `make e2e` at the repository root runs the same server
against live containers.

Two notes for anyone extending this. The `mcp` SDK depends on `httpx2`, a
different package from classic `httpx`; this client imports `httpx` and declares
it explicitly. And stdio carries the protocol on stdout, so logging goes to
stderr and nothing here may `print`.
