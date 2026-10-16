"""Workflows: several models and transforms chained into one endpoint.

    POST /v1/workflows/{workflow}/run

Same auth, same error contract and same observability as a single-model call -
plus per-node timings, so a slow workflow tells you which stage is slow.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from aperture_core.results import ErrorBody
from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit, telemetry
from app.db import get_session
from app.deps import (
    Page,
    Principal,
    current_user,
    inference_principal,
    pagination,
)
from app.errors import ApiError
from app.metrics import errors_total, request_latency, requests_total
from app.models import Workflow, WorkflowRun
from app.schemas import (
    WorkflowIn,
    WorkflowOut,
    WorkflowPatch,
    WorkflowRunIn,
    WorkflowRunOut,
    WorkflowRunSummary,
    WorkflowValidateOut,
)
from app.serialize import dump, dump_loaded
from app.services.gateway import (
    InputImage,
    Stopwatch,
    _guard_size,
    decode_b64_input,
    fetch_url_image,
    new_request_id,
)
from app.services.workflow import WorkflowEngine, starter_graph, validate_graph
from app.settings import settings

log = logging.getLogger("aperture.workflows")
router = APIRouter(prefix="/workflows", tags=["workflows"])


# --------------------------------------------------------------------- registry


async def _decorate(session: AsyncSession, workflow: Workflow, *, with_stats: bool = True
                    ) -> WorkflowOut:
    stats = {"runs_24h": 0, "error_rate_24h": 0.0, "p95_ms_24h": None}
    if with_stats:
        since = datetime.now(UTC) - timedelta(hours=24)
        row = (
            await session.execute(
                sa.select(
                    sa.func.count(),
                    sa.func.coalesce(
                        sa.func.sum(sa.case((WorkflowRun.status != "ok", 1), else_=0)), 0
                    ),
                    sa.func.percentile_cont(0.95).within_group(WorkflowRun.total_ms.asc()),
                ).where(WorkflowRun.workflow_id == workflow.id, WorkflowRun.created_at >= since)
            )
        ).one()
        runs = int(row[0] or 0)
        stats = {
            "runs_24h": runs,
            "error_rate_24h": round(int(row[1]) / runs, 5) if runs else 0.0,
            "p95_ms_24h": round(float(row[2]), 2) if row[2] is not None else None,
        }

    graph = workflow.graph or {}
    models = sorted({
        (node.get("data") or {}).get("model")
        for node in (graph.get("nodes") or [])
        if (node.get("data") or {}).get("type") == "model" and (node.get("data") or {}).get("model")
    })
    return WorkflowOut.model_validate(
        await dump(session, workflow, problems=validate_graph(graph), models_used=models, **stats)
    )


@router.get("", response_model=list[WorkflowOut])
async def list_workflows(principal: Principal = Depends(current_user),
                         session: AsyncSession = Depends(get_session)) -> list[WorkflowOut]:
    principal.require("viewer")
    rows = (
        await session.execute(
            sa.select(Workflow).where(Workflow.archived_at.is_(None))
            .order_by(Workflow.updated_at.desc())
        )
    ).scalars().all()
    return [await _decorate(session, row) for row in rows]


@router.post("", response_model=WorkflowOut, status_code=201)
async def create_workflow(body: WorkflowIn, request: Request,
                          principal: Principal = Depends(current_user),
                          session: AsyncSession = Depends(get_session)) -> WorkflowOut:
    principal.require("developer")
    existing = (
        await session.execute(
            sa.select(Workflow).where(Workflow.slug == body.slug)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ApiError("slug_taken", f"a workflow '{body.slug}' already exists")

    workflow = Workflow(
        slug=body.slug, name=body.name, description=body.description,
        owner=body.owner, labels=body.labels, graph=body.graph or starter_graph(),
        created_by=principal.id,
    )
    session.add(workflow)
    await session.flush()
    await audit.record(session, action="workflow.create", actor_id=principal.id,
                       actor_label=principal.label,
                       target_type="workflow", target_id=workflow.id, target_label=workflow.slug,
                       client_ip=request.client.host if request.client else None)
    await session.commit()
    return await _decorate(session, workflow)


@router.get("/{slug}", response_model=WorkflowOut)
async def get_workflow(slug: str,
                       principal: Principal = Depends(current_user),
                       session: AsyncSession = Depends(get_session)) -> WorkflowOut:
    principal.require("viewer")
    return await _decorate(session, await _find(session, slug))


@router.patch("/{slug}", response_model=WorkflowOut)
async def update_workflow(slug: str, body: WorkflowPatch, request: Request,
                          principal: Principal = Depends(current_user),
                          session: AsyncSession = Depends(get_session)) -> WorkflowOut:
    """Saving the canvas. The draft is free to be broken; publishing is not."""
    principal.require("developer")
    workflow = await _find(session, slug)
    changes = body.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(workflow, key, value)
    if "graph" in changes and workflow.status == "published":
        # the published snapshot keeps serving until the draft is published again
        workflow.status = "draft"
    await audit.record(session, action="workflow.update", actor_id=principal.id,
                       actor_label=principal.label,
                       target_type="workflow", target_id=workflow.id, target_label=workflow.slug,
                       payload={"fields": sorted(changes)},
                       client_ip=request.client.host if request.client else None)
    await session.commit()
    return await _decorate(session, workflow)


@router.post("/{slug}/validate", response_model=WorkflowValidateOut)
async def validate(slug: str,
                   principal: Principal = Depends(current_user),
                   session: AsyncSession = Depends(get_session)) -> WorkflowValidateOut:
    principal.require("viewer")
    workflow = await _find(session, slug)
    problems = validate_graph(workflow.graph or {})
    return WorkflowValidateOut(ok=not problems, problems=problems)


@router.post("/{slug}/publish", response_model=WorkflowOut)
async def publish(slug: str, request: Request,
                  principal: Principal = Depends(current_user),
                  session: AsyncSession = Depends(get_session)) -> WorkflowOut:
    """Snapshot the draft as what callers get. Refuses an invalid graph."""
    principal.require("developer")
    workflow = await _find(session, slug)
    problems = validate_graph(workflow.graph or {})
    if problems:
        raise ApiError(
            "validation_failed",
            "this workflow cannot be published yet: " + "; ".join(problems[:4]),
            details={"problems": problems},
        )
    workflow.published_graph = workflow.graph
    workflow.revision += 1
    workflow.status = "published"
    workflow.published_at = datetime.now(UTC)
    await audit.record(session, action="workflow.publish", actor_id=principal.id,
                       actor_label=principal.label,
                       target_type="workflow", target_id=workflow.id, target_label=workflow.slug,
                       payload={"revision": workflow.revision},
                       client_ip=request.client.host if request.client else None)
    await session.commit()
    return await _decorate(session, workflow)


@router.delete("/{slug}", status_code=204)
async def delete_workflow(slug: str, request: Request,
                          principal: Principal = Depends(current_user),
                          session: AsyncSession = Depends(get_session)) -> None:
    principal.require("admin")
    workflow = await _find(session, slug)
    await audit.record(session, action="workflow.delete", actor_id=principal.id,
                       actor_label=principal.label,
                       target_type="workflow", target_id=workflow.id, target_label=workflow.slug,
                       client_ip=request.client.host if request.client else None)
    await session.delete(workflow)
    await session.commit()


# ------------------------------------------------------------------ execution


@router.post("/{slug}/run", response_model=WorkflowRunOut, response_model_exclude_none=True)
async def run_workflow(
    slug: str,
    request: Request,
    response: Response,
    response_format: str | None = Query(
        None, alias="format",
        description="'aperture' (default) or 'roboflow'; an Output node can override it",
    ),
    principal: Principal = Depends(inference_principal),
    session: AsyncSession = Depends(get_session),
) -> WorkflowRunOut:
    """Run a workflow on one or more images. Accepts multipart or JSON."""
    watch = Stopwatch()
    request_id = getattr(request.state, "request_id", None) or new_request_id()
    trace_id, _ = telemetry.current_ids()
    traceparent = ({"traceparent": request.headers["traceparent"]}
                   if "traceparent" in request.headers else {})

    workflow = await _find(session, slug)
    images, body = await _collect(request)
    _guard_size(sum(len(image.data) for image in images), len(images))

    if body.draft:
        if principal.kind != "user":
            raise ApiError("forbidden", "only a signed-in user can run an unpublished draft")
        graph = workflow.graph or {}
        problems = validate_graph(graph)
        if problems:
            raise ApiError("invalid_config",
                           "this draft cannot run yet: " + "; ".join(problems[:3]),
                           details={"problems": problems})
    else:
        graph = workflow.published_graph or {}
        if not graph:
            raise ApiError(
                "model_not_deployed",
                f"workflow '{slug}' has never been published - publish it, or run the draft "
                "from the editor",
            )

    engine = WorkflowEngine(session, request_id=request_id,
                            trace_headers=traceparent,
                            want_previews=body.include_previews and principal.kind == "user",
                            response_format=(response_format or settings.default_response_format),
                            params=body.params, api_key=principal.api_key)
    with telemetry.start_span("aperture.workflow", {"aperture.workflow": slug,
                                                    "aperture.images": len(images)}):
        result = await engine.run(graph, images)

    status = result.status
    labels = (f"workflow:{slug}", str(workflow.revision), "workflow", status)
    requests_total.labels(*labels).inc()
    request_latency.labels(f"workflow:{slug}", str(workflow.revision), "workflow").observe(
        result.total_ms / 1000
    )
    if result.error_code:
        errors_total.labels(f"workflow:{slug}", str(workflow.revision), result.error_code).inc()

    run = WorkflowRun(
        workflow_id=workflow.id, request_id=request_id, trace_id=trace_id,
        revision=workflow.revision if not body.draft else 0, status=status,
        error_code=result.error_code, error_message=result.error_message,
        failed_node=result.failed_node, caller_type=principal.kind, caller_id=principal.id,
        caller_label=principal.label, image_count=len(images), model_calls=result.model_calls,
        total_ms=round(result.total_ms, 3), node_stats=result.node_stats,
        output=_truncate_outputs(result.outputs),
    )
    session.add(run)
    await session.commit()

    response.headers["x-request-id"] = request_id
    response.headers["x-aperture-workflow-revision"] = str(run.revision)

    return WorkflowRunOut(
        run_id=run.id, request_id=request_id, workflow=slug, revision=run.revision,
        status=status, outputs=result.outputs, node_stats=result.node_stats,
        model_calls=result.model_calls, total_ms=round(result.total_ms, 3),
        failed_node=result.failed_node,
        error=(ErrorBody(code=result.error_code, message=result.error_message or "",
                         request_id=request_id) if result.error_code else None),
        previews=result.previews,
    )


@router.get("/{slug}/runs", response_model=list[WorkflowRunSummary])
async def list_runs(slug: str, page: Page = Depends(pagination),
                    status: str | None = Query(None),
                    principal: Principal = Depends(current_user),
                    session: AsyncSession = Depends(get_session)) -> list[WorkflowRunSummary]:
    principal.require("viewer")
    workflow = await _find(session, slug)
    stmt = sa.select(WorkflowRun).where(WorkflowRun.workflow_id == workflow.id)
    if status:
        stmt = stmt.where(WorkflowRun.status == status)
    rows = (
        await session.execute(
            stmt.order_by(WorkflowRun.created_at.desc()).limit(page.limit).offset(page.offset)
        )
    ).scalars().all()
    return [WorkflowRunSummary.model_validate(dump_loaded(row)) for row in rows]


# ---------------------------------------------------------------------- helpers


async def _find(session: AsyncSession, slug: str) -> Workflow:
    found = (
        await session.execute(
            sa.select(Workflow).where(sa.or_(Workflow.slug == slug, Workflow.id == slug))
        )
    ).scalar_one_or_none()
    if found is None:
        raise ApiError("not_found", f"no workflow '{slug}'")
    return found


async def _collect(request: Request) -> tuple[list[InputImage], WorkflowRunIn]:
    content_type = (request.headers.get("content-type") or "").lower()
    images: list[InputImage] = []

    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        raw_params = form.get("params")
        params = None
        if isinstance(raw_params, str) and raw_params.strip():
            try:
                params = json.loads(raw_params)
            except ValueError as exc:
                raise ApiError("invalid_request",
                               f"the 'params' field is not valid JSON: {exc}") from exc
            if not isinstance(params, dict):
                raise ApiError("invalid_request", "'params' must be a JSON object")
        body = WorkflowRunIn(
            draft=str(form.get("draft", "")).lower() in ("1", "true", "yes"),
            include_previews=str(form.get("include_previews", "")).lower() in ("1", "true", "yes"),
            params=params,
        )
        for field in ("images", "image", "file", "files"):
            for upload in form.getlist(field):
                if hasattr(upload, "read"):
                    images.append(InputImage(data=await upload.read(),
                                             name=getattr(upload, "filename", None) or field,
                                             source="upload"))
        for url in form.getlist("image_url") + form.getlist("image_urls"):
            if isinstance(url, str) and url.strip():
                images.append(await fetch_url_image(url.strip()))
        return images, body

    raw = await request.json() if await request.body() else {}
    body = WorkflowRunIn.model_validate(raw or {})
    payloads = list(body.images or [])
    if body.image:
        payloads.append(body.image)
    for index, payload in enumerate(payloads):
        images.append(decode_b64_input(payload, index))
    for url in list(body.image_urls or []) + ([body.image_url] if body.image_url else []):
        images.append(await fetch_url_image(url))
    return images, body


def _truncate_outputs(outputs: dict, limit: int = 24_000) -> dict:
    import json

    blob = json.dumps(outputs, default=str)
    if len(blob) <= limit:
        return outputs
    return {
        "truncated": True,
        "reason": f"outputs exceeded {limit} bytes; the run response carried the full payload",
        "keys": {key: len(value) if isinstance(value, list) else 1
                 for key, value in outputs.items()},
    }
