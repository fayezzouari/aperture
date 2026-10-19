"""Workflows: definitions, validation, runs and per-node stats."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from aperture_core.results import ErrorBody
from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class WorkflowIn(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,94}$")
    name: str
    description: str = ""
    owner: str = ""
    labels: list[str] = Field(default_factory=list)
    graph: dict | None = Field(None, description="{nodes, edges}; a starter graph if omitted")


class WorkflowPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    owner: str | None = None
    labels: list[str] | None = None
    graph: dict | None = None


class WorkflowOut(ORMModel):
    id: str
    workspace_id: str
    slug: str
    name: str
    description: str
    owner: str
    labels: list[str]
    graph: dict
    published_graph: dict | None
    revision: int
    status: str
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None
    problems: list[str] = Field(default_factory=list)
    runs_24h: int = 0
    error_rate_24h: float = 0.0
    p95_ms_24h: float | None = None
    models_used: list[str] = Field(default_factory=list)


class WorkflowValidateOut(BaseModel):
    ok: bool
    problems: list[str] = Field(default_factory=list)


class WorkflowRunIn(BaseModel):
    """JSON form of a workflow run. Multipart works the same way."""

    images: list[str] | None = Field(None, description="base64 payloads or data URLs")
    image: str | None = None
    image_urls: list[str] | None = None
    image_url: str | None = None
    params: dict[str, Any] | None = Field(
        None,
        description="named run parameters the graph can branch on, e.g. "
                    '{"part_names": ["hood"]}. Read by Gate nodes.',
    )
    draft: bool = Field(False, description="run the unpublished graph (the editor does this)")
    include_previews: bool = Field(False, description="return intermediate crops, for the editor")


class NodeStatOut(BaseModel):
    node_id: str
    type: str
    label: str
    status: str
    ms: float
    items_in: int
    items_out: int
    detections: int
    model: str | None = None
    version: int | None = None
    error: str | None = None


class WorkflowRunOut(BaseModel):
    run_id: str
    request_id: str
    workflow: str
    revision: int
    status: str
    outputs: dict[str, Any]
    node_stats: list[NodeStatOut]
    model_calls: int
    total_ms: float
    failed_node: str | None = None
    error: ErrorBody | None = None
    previews: dict[str, list[str]] = Field(default_factory=dict)


class WorkflowRunSummary(ORMModel):
    id: str
    request_id: str
    revision: int
    status: str
    error_code: str | None
    error_message: str | None
    failed_node: str | None
    caller_label: str
    image_count: int
    model_calls: int
    total_ms: float
    node_stats: list
    created_at: datetime
