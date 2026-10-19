"""Roboflow's workflow endpoint, answered by Aperture models.

    POST /infer/workflows/{workspace_id}/{workflow_id}

A product already calling a Roboflow workflow moves onto Aperture by changing one
base URL. Roboflow runs a *workflow* - a graph of model blocks whose outputs land
under fixed keys - while Aperture serves one model per call, so this route is the
fan-out that closes the gap: it runs the models a workflow stands for and
assembles their answers under the keys the caller already parses.

The per-model payload is not re-invented here. `aperture_core.formats.to_roboflow`
already emits Roboflow's prediction shape - `predictions` plus the inference
`image` dimensions, geometry in absolute pixels, an RLE mask traced to `points` -
so this module only decides *which* models run and *what key* each answers under.

This is a migration aid and is off unless `AP_ROBOFLOW_COMPAT_ENABLED` says
otherwise. It is deliberately not mounted under `/v1`: it is Roboflow's contract,
not Aperture's, and should not read as part of the API.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging

import sqlalchemy as sa
from aperture_core.formats import to_roboflow
from aperture_core.results import ImageResult
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps import (
    Principal,
    current_user,
    enter_workspace,
    get_model_or_404,
    narrow_key,
    principal_for_key,
)
from app.errors import ApiError
from app.models import Workflow, Workspace
from app.security import key_allows_model
from app.services.gateway import (
    InputImage,
    Stopwatch,
    _guard_size,
    build_runtime_payload,
    call_runtime,
    new_request_id,
    pick_replica,
    resolve_target,
    sanitise_overrides,
)
from app.services.workflow import WorkflowEngine
from app.settings import settings

log = logging.getLogger("aperture.compat.roboflow")
router = APIRouter(tags=["compat"])

# Which Aperture models a workflow stands for. Matched by prefix: the same
# workflow carries an environment suffix in every deployment, so
# "cells-detection-production" and "cells-detection-staging" are one entry.
#
# "fanout" runs only the models the request's `part_names` asks for, each
# answering under its own name - that is how the cells workflow's Part Router
# behaves. "fixed" runs every model listed, under the key it is listed against.
DEFAULT_WORKFLOWS: dict[str, dict] = {
    "cells-detection": {
        "kind": "fanout",
        "tag": "development",
        "models": {
            "hood": "hood",
            "door_front_left": "door-front-left",
            "door_front_right": "door-front-right",
            "door_back_left": "door-back-left",
            "door_back_right": "door-back-right",
        },
    },
    "part-and-damage-detection": {
        "kind": "fixed",
        "tag": "development",
        # A model may also be written "slug@tag" to pin it to a tag of its own,
        # for when one model reaches an environment ahead of the others.
        "models": {
            "part_detections": "part-detection-v2",
            "damage_detections": "damage-detection",
        },
        # Aperture's rf-detr-seg preset floors scores at 0.5; Roboflow's
        # instance-segmentation block defaults to 0.4 and this workflow does not
        # override it. Matching that default matters: the damage model scores
        # lower than the part model, so 0.5 silently returned nothing at all.
        "overrides": {"damage_detections": {"score_threshold": 0.4}},
        # `is_damage: false` means the caller sent a photo that carries no
        # damage, and Roboflow's graph skips the damage branch for it. Skipping
        # it here too keeps the answer identical and the inference unspent.
        "damage_keys": ["damage_detections"],
    },
}


def _workflows() -> dict[str, dict]:
    raw = (settings.roboflow_compat_workflows or "").strip()
    if not raw:
        return DEFAULT_WORKFLOWS
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise ApiError("invalid_config",
                       f"AP_ROBOFLOW_COMPAT_WORKFLOWS is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ApiError("invalid_config", "AP_ROBOFLOW_COMPAT_WORKFLOWS must be a JSON object")
    return parsed


def _recipe(workflow_id: str) -> dict:
    table = _workflows()
    for prefix, recipe in table.items():
        if workflow_id.startswith(prefix):
            return recipe
    raise ApiError(
        "not_found",
        f"no Aperture models are mapped to the workflow '{workflow_id}'",
        details={"known_prefixes": sorted(table)},
        http_status=404,
    )


async def _published_workflow(session: AsyncSession, workflow_id: str,
                              workspace_id: str | None):
    """A real Aperture workflow standing in for this Roboflow workflow, if there is one.

    Matched the same way as a recipe - by prefix - so `cells-detection-staging`
    finds the `cells-detection` workflow. A published workflow wins over the
    built-in recipe, so the graph someone can see and edit in the UI is the one
    that actually serves, rather than a second definition hidden in this file.
    """
    stmt = sa.select(Workflow)
    if workspace_id is not None:
        stmt = stmt.where(Workflow.workspace_id == workspace_id)
    rows = (await session.execute(stmt)).scalars().all()
    matches = [w for w in rows if workflow_id.startswith(w.slug) and w.published_graph]
    if not matches:
        return None
    # the most specific slug wins, so a `cells-detection-staging` workflow would
    # take precedence over a generic `cells-detection` one
    return max(matches, key=lambda w: len(w.slug))


def _decode_image(value: object) -> InputImage:
    """Roboflow's image input: {"type": "base64", "value": "..."}."""
    if not isinstance(value, dict):
        raise ApiError("invalid_request",
                       "inputs.image must be an object like "
                       '{"type": "base64", "value": "..."}')
    kind = value.get("type")
    payload = value.get("value")
    if kind != "base64" or not isinstance(payload, str) or not payload:
        raise ApiError(
            "invalid_request",
            f"inputs.image.type '{kind}' is not supported here; send "
            '{"type": "base64", "value": "..."}',
        )
    try:
        data = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ApiError("invalid_image", f"inputs.image.value is not valid base64: {exc}") from exc
    if not data:
        raise ApiError("invalid_image", "inputs.image.value decoded to zero bytes")
    return InputImage(data=data, name="image", source="base64")


def _selected(recipe: dict, inputs: dict) -> dict[str, str]:
    """The {output key: model slug} pairs this request actually runs."""
    models: dict[str, str] = dict(recipe.get("models") or {})
    if recipe.get("kind") == "fanout":
        wanted = inputs.get("part_names")
        if not isinstance(wanted, list):
            raise ApiError("invalid_request",
                           "inputs.part_names must be a list of part names for this workflow")
        unknown = [name for name in wanted if name not in models]
        if unknown:
            # Loud rather than silent: an unmapped name means the caller expects
            # a model this instance does not have, and returning an empty slot
            # for it would read as "nothing detected".
            raise ApiError(
                "not_found",
                f"no Aperture model is mapped to part_names {unknown}",
                details={"known": sorted(models)},
                http_status=404,
            )
        return {name: models[name] for name in wanted}

    if inputs.get("is_damage") is False:
        for key in recipe.get("damage_keys") or []:
            models.pop(key, None)
    return models


async def _run_one(request: Request, session: AsyncSession, principal: Principal,
                   slug: str, ref: str, image: InputImage,
                   request_id: str, overrides: dict | None = None) -> dict:
    """One model's answer, already in Roboflow's per-block shape."""
    model_row = await get_model_or_404(session, slug, principal.workspace_id)

    if principal.kind == "api_key" and principal.api_key is not None:
        if not key_allows_model(principal.api_key, model_row.id, model_row.slug):
            raise ApiError(
                "key_not_scoped_to_model",
                f"this API key is not scoped to '{model_row.slug}'",
                details={"model": model_row.slug},
            )

    watch = Stopwatch()
    _resolved, target, _shadows = await resolve_target(session, model_row, ref)
    replica = await pick_replica(target.version.id, model_row.slug)
    payload = build_runtime_payload(
        target, [image], request_id=request_id,
        overrides=sanitise_overrides(overrides or {}),
        want_overlay=False, extra=None,
        deadline_ms=target.deployment.request_timeout_ms,
    )
    reply = await call_runtime(
        replica, payload, timeout_ms=target.deployment.request_timeout_ms,
        trace_headers=(
            {"traceparent": request.headers["traceparent"]}
            if "traceparent" in request.headers else {}
        ),
    )

    parsed = [ImageResult.model_validate(r) for r in reply.results]
    if not parsed:
        raise ApiError("inference_failed", f"'{slug}' returned no result for the image")
    return to_roboflow(
        parsed[0],
        task=(target.version.config or {}).get("task") or model_row.task,
        decoder=(target.version.config or {}).get("postprocess", {}).get("decoder", ""),
        inference_id=request_id,
        time_s=watch.ms() / 1000,
        model=model_row.slug,
        version=target.version.version,
    )


@router.post(
    "/infer/workflows/{workspace_id}/{workflow_id}",
    summary="Run a Roboflow workflow's Aperture stand-ins and answer in Roboflow's shape",
)
async def run_workflow(
    workspace_id: str,
    workflow_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    if not settings.roboflow_compat_enabled:
        raise ApiError(
            "not_found",
            "the Roboflow compatibility route is off; set AP_ROBOFLOW_COMPAT_ENABLED=true",
            http_status=404,
        )

    try:
        body = await request.json()
    except ValueError as exc:
        raise ApiError("invalid_request", f"the request body is not valid JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise ApiError("invalid_request", "the request body must be a JSON object")

    # Roboflow's clients carry the key in the body, so that is where it is read
    # from - falling back to Aperture's own header for anything already using it.
    raw_key = request.headers.get("x-api-key") or body.get("api_key") or ""
    # Roboflow names its workspace in the path; when an Aperture workspace has
    # that slug it is the one meant, otherwise the header decides, as on /v1
    named = (
        await session.execute(sa.select(Workspace.slug).where(Workspace.slug == workspace_id))
    ).scalar_one_or_none()
    if raw_key:
        principal = await principal_for_key(request, session, str(raw_key))
        principal = await narrow_key(request, principal, session, named)
    else:
        principal = await current_user(request, session)
        principal = await enter_workspace(request, principal, session, named)

    inputs = body.get("inputs")
    if not isinstance(inputs, dict):
        raise ApiError("invalid_request", "the request body must carry an 'inputs' object")

    image = _decode_image(inputs.get("image"))
    _guard_size(len(image.data), 1)
    request_id = getattr(request.state, "request_id", None) or new_request_id()

    # A workflow someone built in Aperture is the better answer than the recipe
    # baked into this module: it is visible, editable and version-tracked. The
    # recipe stays as the fallback for instances that never defined one.
    managed = await _published_workflow(session, workflow_id, principal.workspace_id)
    if managed is not None:
        params = {k: v for k, v in inputs.items() if k != "image"}
        engine = WorkflowEngine(session, request_id=request_id,
                                response_format="roboflow", params=params,
                                api_key=principal.api_key,
                                workspace_id=managed.workspace_id)
        result = await engine.run(managed.published_graph, [image])
        if result.status != "ok":
            raise ApiError(result.error_code or "inference_failed",
                           result.error_message or "the workflow failed",
                           details={"workflow": managed.slug,
                                    "failed_node": result.failed_node})
        # the engine keys outputs per node and holds a list per image; this route
        # answers about one image, so unwrap it into Roboflow's single-run entry
        outputs = {k: (v[0] if isinstance(v, list) and v else v)
                   for k, v in (result.outputs or {}).items()}
        log.info("roboflow compat served by an Aperture workflow",
                 extra={"workflow_id": workflow_id, "aperture_workflow": managed.slug,
                        "model_calls": result.model_calls, "request_id": request_id})
        return {"outputs": [outputs]}

    recipe = _recipe(workflow_id)
    selected = _selected(recipe, inputs)
    default_ref = str(recipe.get("tag") or "production")

    # Sequential on purpose: these models are large and a replica holds them
    # resident, so firing five at once is how a runtime gets OOM-killed rather
    # than how the answer arrives sooner.
    outputs: dict[str, dict] = {}
    for key, spec in selected.items():
        slug, _, pinned = spec.partition("@")
        outputs[key] = await _run_one(
            request, session, principal, slug, pinned or default_ref, image,
            f"{request_id}-{key}", (recipe.get("overrides") or {}).get(key),
        )

    log.info("roboflow compat workflow served",
             extra={"workflow_id": workflow_id, "workspace_id": workspace_id,
                    "models": list(selected.values()), "request_id": request_id})

    # Roboflow answers with one entry per workflow run; there is one run here.
    return {"outputs": [outputs]}
