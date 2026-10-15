"""Read-only tools: the registry, the fleet, the traffic, the audit trail.

Every tool here is a GET against /v1 behind the caller's own roles. `project`
overrides the default project slug for one call; lists page with limit/offset
and return `next_offset` when there may be more.
"""

from __future__ import annotations

from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver import Image
from mcp.types import ToolAnnotations

from .. import trim
from ..client import ApertureError
from ..errors import guarded
from ..images import to_image
from ..state import AppState

READ = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)


def _limit(limit: int) -> int:
    return max(1, min(int(limit), 500))


def register_read_tools(mcp: MCPServer, state: AppState) -> None:
    client = state.client
    guard = guarded(state.settings)

    # ------------------------------------------------------------- projects

    @mcp.tool(annotations=READ)
    @guard
    async def list_projects() -> dict[str, Any]:
        """Projects this token can see, with the caller's role in each. Project slugs are
        what `project=` takes on every other tool."""
        rows = await client.get("/v1/projects")
        return {"items": [trim.project_row(r) for r in rows], "default": state.settings.project}

    # --------------------------------------------------------------- models

    @mcp.tool(annotations=READ)
    @guard
    async def list_models(q: str | None = None, task: str | None = None,
                          tag: str | None = None, deployed: bool | None = None,
                          include_archived: bool = False, limit: int = 50, offset: int = 0,
                          project: str | None = None) -> dict[str, Any]:
        """Models in the project with what is live and how each behaved in the last 24 h.
        `q` matches slug, name, owner or description; `tag` keeps models carrying that tag
        (e.g. production); `deployed=true` keeps models with at least one live replica."""
        rows = await client.get("/v1/models", project=project, params={
            "q": q, "task": task, "tag": tag, "deployed": deployed,
            "include_archived": include_archived, "limit": _limit(limit), "offset": offset,
        })
        return trim.page([trim.model_row(r) for r in rows], _limit(limit), offset)

    @mcp.tool(annotations=READ)
    @guard
    async def get_model(ref: str, project: str | None = None) -> dict[str, Any]:
        """One model as a card: its tags, every version with status, and the live
        deployments with replica readiness. `ref` is the model slug or id."""
        model = await client.get(f"/v1/models/{ref}", project=project)
        versions = await client.get(f"/v1/models/{ref}/versions", project=project)
        deployments = await client.get(f"/v1/models/{ref}/deployments", project=project)
        return {
            "model": trim.model_row(model),
            "description": model.get("description") or "",
            "versions": [trim.version_row(v) for v in versions],
            "deployments": [trim.deployment_row(d) for d in deployments],
        }

    @mcp.tool(annotations=READ)
    @guard
    async def list_versions(ref: str, project: str | None = None) -> dict[str, Any]:
        """Every version of a model: status (draft, ready, published, failed...), preset,
        artifact format, dataset, metrics, changelog and which tags point at it."""
        rows = await client.get(f"/v1/models/{ref}/versions", project=project)
        return {"items": [trim.version_row(v) for v in rows], "count": len(rows)}

    @mcp.tool(annotations=READ)
    @guard
    async def get_version(ref: str, version: int, include_config: bool = True,
                          project: str | None = None) -> dict[str, Any]:
        """One version in full. With `include_config` the preprocessing, decoder, thresholds
        and class list that travel with it are returned - that is what to read when a
        model behaves oddly."""
        row = await client.get(f"/v1/models/{ref}/versions/{version}", project=project)
        return trim.version_detail(row, include_config)

    @mcp.tool(annotations=READ)
    @guard
    async def compare_versions(ref: str, left: int, right: int,
                               project: str | None = None) -> dict[str, Any]:
        """Side-by-side of two versions: config differences, metric differences and the
        traffic each has served."""
        row = await client.get(f"/v1/models/{ref}/version-compare", project=project,
                               params={"left": left, "right": right})
        return {
            "left": trim.version_row(row.get("left") or {}),
            "right": trim.version_row(row.get("right") or {}),
            **trim.pick(row, "config_diff", "metric_diff", "traffic"),
        }

    # ------------------------------------------------------------ operations

    @mcp.tool(annotations=READ)
    @guard
    async def get_overview(project: str | None = None) -> dict[str, Any]:
        """The project's health page: counts, replicas ready vs desired, request/error/latency
        summary, every runtime, the most recent failures and firing alerts. Start here."""
        return trim.overview(await client.get("/v1/overview", project=project))

    @mcp.tool(annotations=READ)
    @guard
    async def get_metrics(window_minutes: int = 60, model_id: str | None = None,
                          version_id: str | None = None, include_series: bool = False,
                          project: str | None = None) -> dict[str, Any]:
        """Requests, errors, error rate, images, rps and p50/p95/p99 over a window, with
        per-stage latency and a status breakdown. Narrow to a model or version by id;
        `include_series` adds the time series the chart is drawn from."""
        row = await client.get("/v1/metrics/summary", project=project, params={
            "window_minutes": window_minutes, "model_id": model_id, "version_id": version_id,
        })
        return trim.metrics_summary(row, include_series)

    @mcp.tool(annotations=READ)
    @guard
    async def get_errors(since_hours: int = 24, model_id: str | None = None,
                         project: str | None = None) -> dict[str, Any]:
        """Error breakdown by code and model over the period."""
        rows = await client.get("/v1/metrics/errors", project=project,
                                params={"since_hours": since_hours, "model_id": model_id})
        return {"items": rows, "since_hours": since_hours}

    @mcp.tool(annotations=READ)
    @guard
    async def search_inferences(model_id: str | None = None, version_id: str | None = None,
                                tag: str | None = None, status: str | None = None,
                                error_code: str | None = None, route: str | None = None,
                                request_id: str | None = None, trace_id: str | None = None,
                                min_latency_ms: float | None = None,
                                max_latency_ms: float | None = None,
                                min_confidence: float | None = None,
                                max_confidence: float | None = None,
                                flagged: bool | None = None, has_capture: bool | None = None,
                                since_hours: int = 24, limit: int = 50, offset: int = 0,
                                project: str | None = None) -> dict[str, Any]:
        """The inference log. `status` is ok | error | timeout | rejected; `route` is
        primary | canary | shadow; `has_capture=true` keeps rows whose image was stored,
        which `get_inference` can then show. Newest first."""
        rows = await client.get("/v1/inferences", project=project, params={
            "model_id": model_id, "version_id": version_id, "tag": tag, "status": status,
            "error_code": error_code, "route": route, "request_id": request_id,
            "trace_id": trace_id, "min_latency_ms": min_latency_ms,
            "max_latency_ms": max_latency_ms, "min_confidence": min_confidence,
            "max_confidence": max_confidence, "flagged": flagged, "has_capture": has_capture,
            "since_hours": since_hours, "limit": _limit(limit), "offset": offset,
        })
        return trim.page([trim.inference_row(r) for r in rows], _limit(limit), offset)

    @mcp.tool(annotations=READ, structured_output=False)
    @guard
    async def get_inference(log_id: str, include_images: bool = True, max_images: int = 2,
                            project: str | None = None) -> list[str | Image]:
        """One logged inference with its full output and, when the request was captured,
        the input image and the rendered overlay as images you can look at."""
        row = await client.get(f"/v1/inferences/{log_id}", project=project)
        parts: list[str | Image] = [_json(trim.inference_detail(row))]
        if include_images:
            urls = _interleave(row.get("image_urls") or [], row.get("overlay_urls") or [])
            for url in urls[: max(0, max_images) * 2]:
                data = await client.fetch_bytes(url)
                parts.append(to_image(data, state.settings.max_image_px))
        return parts

    @mcp.tool(annotations=READ)
    @guard
    async def get_drift(version_id: str, windows: int = 48,
                        project: str | None = None) -> dict[str, Any]:
        """Input and confidence drift for a version: the baseline window, the current one,
        the rolled-up windows and any signals that fired."""
        return await client.get(f"/v1/drift/{version_id}", project=project,
                                params={"windows": windows})

    @mcp.tool(annotations=READ)
    @guard
    async def list_runtimes(project: str | None = None) -> dict[str, Any]:
        """The fleet: every runtime with device, memory, what it holds and when it last
        reported. `fresh=false` means its heartbeat is stale."""
        rows = await client.get("/v1/runtimes", project=project)
        return {"items": [trim.runtime_row(r) for r in rows], "count": len(rows)}

    # ----------------------------------------------------------------- jobs

    @mcp.tool(annotations=READ)
    @guard
    async def list_jobs(kind: str | None = None, status: str | None = None, limit: int = 50,
                        offset: int = 0, project: str | None = None) -> dict[str, Any]:
        """Background jobs: bulk and video inference, conversions, exports, imports."""
        rows = await client.get("/v1/jobs", project=project, params={
            "kind": kind, "status": status, "limit": _limit(limit), "offset": offset,
        })
        return trim.page([trim.job_row(r) for r in rows], _limit(limit), offset)

    @mcp.tool(annotations=READ)
    @guard
    async def get_job(job_id: str, project: str | None = None) -> dict[str, Any]:
        """One job with progress, error and - once finished - its result."""
        row = await client.get(f"/v1/jobs/{job_id}", project=project)
        out = trim.job_row(row)
        if row.get("status") in ("succeeded", "failed", "cancelled"):
            out["result"] = row.get("result") or {}
            if row.get("status") == "succeeded":
                try:
                    out["result"] = await client.get(f"/v1/jobs/{job_id}/result", project=project)
                except ApertureError as exc:  # the inline result is still worth returning
                    out["result_note"] = f"full result unavailable: {exc}"
        return out

    # ------------------------------------------------------------ workflows

    @mcp.tool(annotations=READ)
    @guard
    async def list_workflows(project: str | None = None) -> dict[str, Any]:
        """Workflows: several models served as one endpoint. Shows status, the models each
        uses and 24 h traffic."""
        rows = await client.get("/v1/workflows", project=project)
        return {"items": [trim.workflow_row(r) for r in rows], "count": len(rows)}

    @mcp.tool(annotations=READ)
    @guard
    async def get_workflow(slug: str, include_graph: bool = True,
                           project: str | None = None) -> dict[str, Any]:
        """One workflow, with its node graph (input, gates, models, crops, results)."""
        row = await client.get(f"/v1/workflows/{slug}", project=project)
        return trim.workflow_row(row, include_graph)

    # ------------------------------------------------------------ reference

    @mcp.tool(annotations=READ)
    @guard
    async def list_presets() -> dict[str, Any]:
        """Architecture presets a new version can start from (YOLO, RF-DETR, ResNet, SAM...)."""
        rows = await client.get("/v1/meta/presets")
        return {"items": rows, "count": len(rows)}

    @mcp.tool(annotations=READ)
    @guard
    async def get_preset(name: str) -> dict[str, Any]:
        """The full default config for one preset."""
        return await client.get(f"/v1/meta/presets/{name}")

    # ---------------------------------------------------------------- audit

    @mcp.tool(annotations=READ)
    @guard
    async def search_audit(action: str | None = None, actor: str | None = None,
                           target_type: str | None = None, since_hours: int = 24 * 30,
                           limit: int = 50, offset: int = 0,
                           project: str | None = None) -> dict[str, Any]:
        """Who changed what: every mutating action in the project, newest first. `action`
        is exact (e.g. tag.move, deployment.rollback); `actor` matches the label."""
        rows = await client.get("/v1/audit", project=project, params={
            "action": action, "actor": actor, "target_type": target_type,
            "since_hours": since_hours, "limit": _limit(limit), "offset": offset,
        })
        return trim.page([trim.audit_row(r) for r in rows], _limit(limit), offset)


def _interleave(images: list[str], overlays: list[str]) -> list[str]:
    out: list[str] = []
    for index in range(max(len(images), len(overlays))):
        if index < len(images):
            out.append(images[index])
        if index < len(overlays):
            out.append(overlays[index])
    return out


def _json(payload: Any) -> str:
    import json

    return json.dumps(payload, indent=1, default=str)
