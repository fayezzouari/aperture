# aperture-mcp

Aperture's API as MCP tools, over stdio. Point a coding agent at an Aperture
instance and it can read the registry, the fleet, the traffic and the audit log,
run an image through a model, and *look* at the overlay that came back.

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

Claude Desktop and Cursor take the same three variables as JSON; the README at
the repository root has that form.

| Variable | |
|---|---|
| `APERTURE_URL` | required, e.g. `http://localhost:8080` |
| `APERTURE_TOKEN` | required. `ap_pat_…` for everything; an `ap_…` API key registers only `predict` and `run_workflow`, because that is all a key can reach |
| `APERTURE_WORKSPACE` | optional default workspace slug; every tool also takes `workspace=`. Unset, a user's calls act in the workspace they opened last in the console. An API key acts in the workspace it belongs to, so leave it unset or set it to that workspace |

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
