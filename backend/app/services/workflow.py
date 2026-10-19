"""The workflow engine: a DAG of model calls and transforms, run as one request.

Why this exists: a useful answer usually needs more than one model. "Which panel
is damaged and how badly" is a detector, a crop, and a classifier that also wants
the damage box geometry. Without workflows every product re-implements that
chaining - and each one drifts.

The data model is deliberately small. Every node consumes and produces a list of
`Item`s, where an item is one image plus where it came from and what has been
computed about it. That makes fan-out (one photo -> six crops) and fan-in (six
crops -> one summary) fall out of the same machinery, and it means a model node
never has to know whether it is looking at a whole photo or a crop.

    input ──▶ model(part-detection) ──▶ crop ──▶ model(damage-groups) ──▶ output
                                        │            ▲
                                        └── geometry ┘   (the crop's box as aux)
"""

from __future__ import annotations

import base64
import io
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from aperture_core.formats import FORMATS, to_roboflow
from aperture_core.results import ImageResult
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ApiError
from app.security import key_allows_model
from app.services.gateway import (
    InputImage,
    build_runtime_payload,
    dispatch,
    resolve_target,
    sanitise_overrides,
)

log = logging.getLogger("aperture.workflow")

NODE_TYPES = ("input", "model", "gate", "crop", "filter", "geometry", "merge", "output")
MAX_ITEMS_PER_NODE = 64        # a fan-out guard: a bad threshold should not melt the fleet
MAX_NODES = 40
CROP_JPEG_QUALITY = 92


# --------------------------------------------------------------------- values


@dataclass
class Item:
    """One image travelling through the graph, plus what is known about it."""

    image: bytes
    name: str
    # where this item came from, when it is a crop of something bigger
    source_index: int = 0
    box: list[float] | None = None            # in the *parent* image's pixels
    parent_size: tuple[int, int] | None = None  # (width, height) of the parent
    class_name: str | None = None
    score: float | None = None
    inputs: dict[str, list[float]] = field(default_factory=dict)  # named model inputs

    def as_input_image(self) -> InputImage:
        return InputImage(data=self.image, name=self.name, source="workflow")


@dataclass
class NodeOutput:
    items: list[Item] = field(default_factory=list)
    results: list[dict] = field(default_factory=list)   # per item, when a model ran
    detail: dict = field(default_factory=dict)


@dataclass
class NodeStat:
    node_id: str
    type: str
    label: str
    status: str = "ok"
    ms: float = 0.0
    items_in: int = 0
    items_out: int = 0
    detections: int = 0
    model: str | None = None
    version: int | None = None
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            "node_id": self.node_id, "type": self.type, "label": self.label,
            "status": self.status, "ms": round(self.ms, 2), "items_in": self.items_in,
            "items_out": self.items_out, "detections": self.detections,
            "model": self.model, "version": self.version, "error": self.error,
        }


@dataclass
class RunResult:
    status: str
    outputs: dict[str, Any]
    node_stats: list[dict]
    model_calls: int
    total_ms: float
    failed_node: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    previews: dict[str, list[str]] = field(default_factory=dict)


# ----------------------------------------------------------------- validation


def validate_graph(graph: dict) -> list[str]:
    """Everything the editor should refuse to publish, in plain language."""
    problems: list[str] = []
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []

    if not nodes:
        return ["the workflow is empty - start with an Input node"]
    if len(nodes) > MAX_NODES:
        problems.append(f"{len(nodes)} nodes exceeds the limit of {MAX_NODES}")

    ids = [n.get("id") for n in nodes]
    if len(set(ids)) != len(ids):
        problems.append("two nodes share the same id")

    by_id = {n["id"]: n for n in nodes if n.get("id")}
    for node in nodes:
        kind = (node.get("data") or {}).get("type") or node.get("type")
        if kind not in NODE_TYPES:
            problems.append(f"node '{node.get('id')}' has unknown type '{kind}'")
            continue
        data = node.get("data") or {}
        if kind == "model" and not data.get("model"):
            problems.append(f"the model node '{data.get('label') or node['id']}' has no model set")
        if kind == "gate" and not data.get("param"):
            problems.append(
                f"the gate node '{data.get('label') or node['id']}' does not say which "
                "run parameter it reads")
        if kind == "filter" and not (data.get("classes") or data.get("min_score") is not None):
            problems.append(f"the filter node '{node['id']}' filters on nothing")

    kinds = [(n.get("data") or {}).get("type") for n in nodes]
    if "input" not in kinds:
        problems.append("there is no Input node, so nothing can enter the workflow")
    if kinds.count("input") > 1:
        problems.append("there is more than one Input node")
    if "output" not in kinds:
        problems.append("there is no Output node, so the workflow would return nothing")

    for edge in edges:
        if edge.get("source") not in by_id or edge.get("target") not in by_id:
            problems.append("an edge points at a node that does not exist")

    # a cycle would never terminate
    order, cyclic = topological_order(by_id, edges)
    if cyclic:
        problems.append("the graph has a cycle - a workflow has to flow one way")

    reachable = {n for n in order}
    for node in nodes:
        kind = (node.get("data") or {}).get("type")
        if kind in ("model", "gate", "crop", "filter", "geometry", "merge") \
                and node["id"] in reachable:
            if not any(e.get("target") == node["id"] for e in edges):
                label = (node.get("data") or {}).get("label") or node["id"]
                problems.append(f"'{label}' has no input connected")

    return problems


def topological_order(by_id: dict, edges: list[dict]) -> tuple[list[str], bool]:
    incoming = {node_id: 0 for node_id in by_id}
    children: dict[str, list[str]] = {node_id: [] for node_id in by_id}
    for edge in edges:
        source, target = edge.get("source"), edge.get("target")
        if source in by_id and target in by_id:
            incoming[target] += 1
            children[source].append(target)

    queue = [node_id for node_id, count in incoming.items() if count == 0]
    order: list[str] = []
    while queue:
        node_id = queue.pop(0)
        order.append(node_id)
        for child in children[node_id]:
            incoming[child] -= 1
            if incoming[child] == 0:
                queue.append(child)
    return order, len(order) != len(by_id)


# ------------------------------------------------------------------ execution


class WorkflowEngine:
    def __init__(self, session: AsyncSession, *, request_id: str,
                 trace_headers: dict[str, str] | None = None,
                 want_previews: bool = False, response_format: str = "aperture",
                 params: dict | None = None, api_key=None, workspace_id: str | None = None):
        self.session = session
        self.workspace_id = workspace_id  # model nodes resolve inside the workflow's workspace
        self.api_key = api_key  # set when an API key called: model nodes honour its scope
        self.request_id = request_id
        self.trace_headers = trace_headers or {}
        self.want_previews = want_previews
        self.params = params or {}
        self.response_format = response_format if response_format in FORMATS else "aperture"
        self.model_calls = 0
        self.stats: list[NodeStat] = []
        self.previews: dict[str, list[str]] = {}

    async def run(self, graph: dict, images: list[InputImage]) -> RunResult:
        started = time.perf_counter()
        nodes = {n["id"]: n for n in (graph.get("nodes") or [])}
        edges = graph.get("edges") or []
        order, cyclic = topological_order(nodes, edges)
        if cyclic:
            raise ApiError("invalid_config", "this workflow graph has a cycle")

        parents: dict[str, list[str]] = {node_id: [] for node_id in nodes}
        for edge in edges:
            if edge.get("source") in nodes and edge.get("target") in nodes:
                parents[edge["target"]].append(edge["source"])

        produced: dict[str, NodeOutput] = {}
        outputs: dict[str, Any] = {}
        failed_node = None
        error: ApiError | None = None

        for node_id in order:
            node = nodes[node_id]
            data = node.get("data") or {}
            kind = data.get("type")
            label = data.get("label") or kind or node_id
            upstream = [produced[p] for p in parents[node_id] if p in produced]
            stat = NodeStat(node_id=node_id, type=kind or "?", label=label,
                            items_in=sum(len(u.items) for u in upstream))
            node_started = time.perf_counter()
            try:
                if kind == "input":
                    result = NodeOutput(items=[
                        Item(image=image.data, name=image.name, source_index=index)
                        for index, image in enumerate(images)
                    ])
                elif kind == "model":
                    result = await self._run_model(data, upstream, stat)
                elif kind == "gate":
                    result = self._run_gate(data, upstream)
                elif kind == "crop":
                    result = self._run_crop(data, upstream)
                elif kind == "filter":
                    result = self._run_filter(data, upstream)
                elif kind == "geometry":
                    result = self._run_geometry(data, upstream)
                elif kind == "merge":
                    result = self._run_merge(upstream)
                elif kind == "output":
                    result = self._run_output(data, upstream, outputs, label)
                else:
                    raise ApiError("invalid_config", f"node type '{kind}' is not supported")

                stat.items_out = len(result.items)
                stat.detections = sum(
                    len(r.get("detections") or []) + len(r.get("classifications") or [])
                    for r in result.results
                )
                produced[node_id] = result

                if self.want_previews and result.items and kind in ("crop", "input"):
                    self.previews[node_id] = [
                        base64.b64encode(item.image).decode() for item in result.items[:6]
                    ]
            except ApiError as exc:
                stat.status, stat.error = "error", exc.message
                failed_node, error = node_id, exc
                self.stats.append(stat)
                break
            except Exception as exc:  # a transform bug should name its node
                stat.status, stat.error = "error", str(exc)[:400]
                failed_node = node_id
                error = ApiError("internal_error",
                                 f"node '{label}' failed: {exc}"[:400])
                self.stats.append(stat)
                break
            finally:
                stat.ms = (time.perf_counter() - node_started) * 1000

            self.stats.append(stat)

        total_ms = (time.perf_counter() - started) * 1000
        return RunResult(
            status="ok" if error is None else "error",
            outputs=outputs,
            node_stats=[s.as_dict() for s in self.stats],
            model_calls=self.model_calls,
            total_ms=total_ms,
            failed_node=failed_node,
            error_code=error.code if error else None,
            error_message=error.message if error else None,
            previews=self.previews,
        )

    # ------------------------------------------------------------- node kinds

    async def _run_model(self, data: dict, upstream: list[NodeOutput],
                         stat: NodeStat) -> NodeOutput:
        from app.deps import get_model_or_404

        items = [item for out in upstream for item in out.items]
        if not items:
            return NodeOutput()
        if len(items) > MAX_ITEMS_PER_NODE:
            raise ApiError(
                "batch_too_large",
                f"this node received {len(items)} images, over the workflow limit of "
                f"{MAX_ITEMS_PER_NODE}. Add a filter or lower the crop limit upstream.",
            )

        model = await get_model_or_404(self.session, data["model"], self.workspace_id)
        if self.api_key is not None and not key_allows_model(self.api_key, model.id, model.slug):
            raise ApiError(
                "key_not_scoped_to_model",
                f"this API key is not scoped to '{model.slug}'",
                details={"model": model.slug},
            )
        ref = data.get("ref") or "production"
        resolved, target, _ = await resolve_target(self.session, model, ref)
        overrides = sanitise_overrides(data.get("overrides"))
        stat.model, stat.version = model.slug, target.version.version

        # Named inputs: whatever a geometry node attached to each item, plus any
        # constants pinned on the node itself.
        named: dict[str, list] = {}
        pinned = data.get("inputs") or {}
        for key, value in pinned.items():
            named[key] = [value] * len(items) if not isinstance(value, list) or \
                not value or not isinstance(value[0], list) else value
        for key in {k for item in items for k in item.inputs}:
            named[key] = [item.inputs.get(key, [0.0, 0.0, 0.0, 0.0]) for item in items]

        payload = build_runtime_payload(
            target, [item.as_input_image() for item in items],
            request_id=f"{self.request_id}:{stat.node_id}", overrides=overrides,
            want_overlay=False, extra=None,
            deadline_ms=target.deployment.request_timeout_ms, inputs=named or None,
        )
        reply = await dispatch(target.version.id, model.slug, payload,
                               timeout_ms=target.deployment.request_timeout_ms,
                               trace_headers=self.trace_headers)
        self.model_calls += 1

        # results stay aligned with the items that produced them
        for item, result in zip(items, reply.results):
            result["_source"] = {"name": item.name, "index": item.source_index,
                                 "box": item.box, "class_name": item.class_name}
        return NodeOutput(
            items=items, results=list(reply.results),
            detail={"model": model.slug, "version": target.version.version,
                    "tag": resolved.tag, "runtime_id": reply.runtime_id,
                    "task": model.task,
                    "decoder": (target.version.config or {}).get("postprocess", {}).get("decoder", "")},
        )

    def _run_crop(self, data: dict, upstream: list[NodeOutput]) -> NodeOutput:
        """Fan out: one crop per detection from the upstream model node."""
        padding = float(data.get("padding", 0.05))
        limit = int(data.get("max_crops", 8))
        min_score = float(data.get("min_score", 0.0))
        classes = set(data.get("classes") or [])
        square = bool(data.get("square", False))

        out: list[Item] = []
        for node_output in upstream:
            for item, result in zip(node_output.items, node_output.results):
                detections = (result.get("detections") or result.get("segments") or [])
                detections = [
                    d for d in detections
                    if d.get("score", 0) >= min_score
                    and (not classes or d.get("class_name") in classes)
                ]
                detections.sort(key=lambda d: -d.get("score", 0))
                if not detections:
                    continue
                image = Image.open(io.BytesIO(item.image)).convert("RGB")
                width, height = image.size
                for detection in detections[:limit]:
                    box = detection.get("box") or []
                    if len(box) != 4:
                        continue
                    crop = _crop(image, box, padding, square)
                    buffer = io.BytesIO()
                    crop.save(buffer, format="JPEG", quality=CROP_JPEG_QUALITY)
                    out.append(Item(
                        image=buffer.getvalue(),
                        name=f"{item.name}#{detection.get('class_name', 'crop')}",
                        source_index=item.source_index,
                        box=[round(float(v), 2) for v in box],
                        parent_size=(width, height),
                        class_name=detection.get("class_name"),
                        score=detection.get("score"),
                    ))
                    if len(out) >= MAX_ITEMS_PER_NODE:
                        break
        return NodeOutput(items=out)

    def _run_gate(self, data: dict, upstream: list[NodeOutput]) -> NodeOutput:
        """Let the branch run only when a run parameter says so.

        `filter` thins detections that a model already produced; a gate decides
        whether the model runs at all. That is the difference between paying for
        six segmentation models on every image and paying for the one part the
        caller actually asked about.

        Closing a gate is not an error and not an empty result: it returns no
        items, and every downstream node already treats "no items" as "nothing
        to do" and passes the emptiness on. So a closed branch costs nothing and
        its Output key is simply absent from the response.
        """
        param = data.get("param")
        if not param:
            raise ApiError("invalid_config", "a Gate node needs a `param` to read")

        actual = (self.params or {}).get(param)
        expected = data.get("value")
        op = (data.get("op") or "contains").lower()

        if op == "contains":
            # the caller sends a list ("which parts?"); the gate names one member
            haystack = actual if isinstance(actual, (list, tuple, set)) else (
                [] if actual is None else [actual])
            passed = expected in haystack
        elif op == "equals":
            passed = actual == expected
        elif op == "truthy":
            passed = bool(actual)
        else:
            raise ApiError("invalid_config",
                           f"a Gate node cannot use the operator '{op}'; "
                           "use contains, equals or truthy")

        return NodeOutput(items=[item for out in upstream for item in out.items],
                          results=[r for out in upstream for r in out.results],
                          detail=dict(upstream[0].detail) if upstream else {}) \
            if passed else NodeOutput()

    def _run_filter(self, data: dict, upstream: list[NodeOutput]) -> NodeOutput:
        classes = set(data.get("classes") or [])
        min_score = data.get("min_score")
        top_k = data.get("top_k")

        items: list[Item] = []
        results: list[dict] = []
        for node_output in upstream:
            for index, result in enumerate(node_output.results):
                kept = dict(result)
                for key in ("detections", "segments", "classifications", "texts"):
                    values = result.get(key)
                    if not values:
                        continue
                    filtered = [
                        v for v in values
                        if (not classes or v.get("class_name") in classes)
                        and (min_score is None or v.get("score", 0) >= float(min_score))
                    ]
                    filtered.sort(key=lambda v: -v.get("score", 0))
                    if top_k:
                        filtered = filtered[: int(top_k)]
                    kept[key] = filtered
                results.append(kept)
                if index < len(node_output.items):
                    items.append(node_output.items[index])
        return NodeOutput(items=items, results=results)

    def _run_geometry(self, data: dict, upstream: list[NodeOutput]) -> NodeOutput:
        """Attach each item's own box, normalised, as a named model input.

        This is the piece that makes a position-aware second-stage model work:
        the crop alone has lost where it came from, and this puts it back.
        """
        name = data.get("input_name") or "aux"
        fmt = data.get("format") or "cxcywh"
        items: list[Item] = []
        for node_output in upstream:
            for item in node_output.items:
                if item.box and item.parent_size:
                    width, height = item.parent_size
                    x1, y1, x2, y2 = item.box
                    if fmt == "xyxy":
                        vector = [x1 / width, y1 / height, x2 / width, y2 / height]
                    else:
                        vector = [((x1 + x2) / 2) / width, ((y1 + y2) / 2) / height,
                                  (x2 - x1) / width, (y2 - y1) / height]
                else:
                    # a whole image is its own box
                    vector = [0.5, 0.5, 1.0, 1.0] if fmt != "xyxy" else [0.0, 0.0, 1.0, 1.0]
                items.append(Item(
                    image=item.image, name=item.name, source_index=item.source_index,
                    box=item.box, parent_size=item.parent_size, class_name=item.class_name,
                    score=item.score,
                    inputs={**item.inputs, name: [round(float(v), 6) for v in vector]},
                ))
        return NodeOutput(items=items)

    def _run_merge(self, upstream: list[NodeOutput]) -> NodeOutput:
        items = [item for out in upstream for item in out.items]
        results = [result for out in upstream for result in out.results]
        return NodeOutput(items=items, results=results)

    def _run_output(self, data: dict, upstream: list[NodeOutput], outputs: dict,
                    label: str) -> NodeOutput:
        key = data.get("key") or label or "result"
        # per-node format wins over the run's, so one workflow can answer a
        # Roboflow-shaped client on one output and a native one on another
        fmt = (data.get("format") or self.response_format or "aperture").lower()
        payload: list[dict] = []
        for node_output in upstream:
            for index, result in enumerate(node_output.results):
                entry = {k: v for k, v in result.items() if k != "raw"}
                source = entry.pop("_source", None)
                if fmt == "roboflow":
                    entry = to_roboflow(
                        ImageResult.model_validate(
                            {k: v for k, v in result.items() if not k.startswith("_")}
                        ),
                        task=node_output.detail.get("task", "detection"),
                        decoder=node_output.detail.get("decoder", ""),
                        inference_id=f"{self.request_id}-{key}-{index}",
                        time_s=0.0,
                        model=node_output.detail.get("model"),
                        version=node_output.detail.get("version"),
                    )
                if source:
                    entry["source"] = source
                payload.append(entry)
            if not node_output.results:
                payload.extend(
                    {"source": {"name": item.name, "index": item.source_index,
                                "box": item.box, "class_name": item.class_name}}
                    for item in node_output.items
                )
        outputs[key] = payload
        merged = self._run_merge(upstream)
        return NodeOutput(items=merged.items, results=merged.results)


def _crop(image: Image.Image, box: list[float], padding: float, square: bool) -> Image.Image:
    width, height = image.size
    x1, y1, x2, y2 = box
    pad_x = (x2 - x1) * padding
    pad_y = (y2 - y1) * padding
    x1, y1, x2, y2 = x1 - pad_x, y1 - pad_y, x2 + pad_x, y2 + pad_y

    if square:
        side = max(x2 - x1, y2 - y1)
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        x1, y1, x2, y2 = cx - side / 2, cy - side / 2, cx + side / 2, cy + side / 2

    x1 = int(max(0, min(x1, width - 1)))
    y1 = int(max(0, min(y1, height - 1)))
    x2 = int(max(x1 + 1, min(x2, width)))
    y2 = int(max(y1 + 1, min(y2, height)))
    return image.crop((x1, y1, x2, y2))


def starter_graph() -> dict:
    """What a new workflow opens with: the simplest thing that runs."""
    return {
        "nodes": [
            {"id": "input", "position": {"x": 40, "y": 120},
             "data": {"type": "input", "label": "Images in"}},
            {"id": "model", "position": {"x": 300, "y": 120},
             "data": {"type": "model", "label": "Detect", "model": "", "ref": "production"}},
            {"id": "output", "position": {"x": 580, "y": 120},
             "data": {"type": "output", "label": "Result", "key": "detections"}},
        ],
        "edges": [
            {"id": "e1", "source": "input", "target": "model"},
            {"id": "e2", "source": "model", "target": "output"},
        ],
    }
