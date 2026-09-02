"""API request/response models. These generate the OpenAPI spec the SDKs use (R3.9)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from aperture_core.configspec import ModelConfig
from aperture_core.results import ErrorBody

Role = Literal["viewer", "developer", "admin"]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------------------ auth


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: "UserOut"


class UserOut(ORMModel):
    id: str
    email: str
    name: str
    provider: str
    is_superadmin: bool
    is_active: bool
    last_login_at: datetime | None = None
    roles: dict[str, str] = Field(default_factory=dict)


class AuthMethods(BaseModel):
    password: bool
    oidc: bool
    ldap: bool
    oidc_login_url: str | None = None
    instance_name: str


# --------------------------------------------------------------------- project


class ProjectIn(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    name: str
    description: str = ""
    capture_sample_rate: float | None = Field(None, ge=0, le=1)
    capture_retention_days: int | None = Field(None, ge=1, le=3650)


class ProjectPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    capture_sample_rate: float | None = Field(None, ge=0, le=1)
    capture_retention_days: int | None = Field(None, ge=1, le=3650)


class ProjectOut(ORMModel):
    id: str
    slug: str
    name: str
    description: str
    capture_sample_rate: float | None
    capture_retention_days: int | None
    created_at: datetime
    model_count: int = 0
    role: str | None = None


class MemberIn(BaseModel):
    email: str
    role: Role = "viewer"


class MemberOut(BaseModel):
    user_id: str
    email: str
    name: str
    role: Role
    provider: str


# ----------------------------------------------------------------------- model


class ModelIn(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,94}$")
    name: str
    task: Literal["detection", "classification", "segmentation", "keypoints", "ocr", "embedding"]
    owner: str = ""
    framework: str = "onnx"
    description: str = ""
    labels: list[str] = Field(default_factory=list)


class ModelPatch(BaseModel):
    name: str | None = None
    owner: str | None = None
    description: str | None = None
    labels: list[str] | None = None


class TagOut(ORMModel):
    id: str
    name: str
    version_id: str
    version: int | None = None
    previous_version_id: str | None = None
    locked: bool
    updated_at: datetime
    updated_by: str | None = None


class ModelOut(ORMModel):
    id: str
    project_id: str
    slug: str
    name: str
    task: str
    owner: str
    framework: str
    description: str
    labels: list[str]
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None
    version_count: int = 0
    latest_version: int | None = None
    tags: list[TagOut] = Field(default_factory=list)
    deployed_versions: list[int] = Field(default_factory=list)
    replicas_ready: int = 0
    replicas_desired: int = 0
    requests_24h: int = 0
    error_rate_24h: float = 0.0
    p95_ms_24h: float | None = None


# --------------------------------------------------------------------- version


class VersionCreate(BaseModel):
    """Metadata for a new version. Weights arrive through the upload endpoints."""

    preset: str | None = Field(None, description="config preset to start from (R1.5)")
    inherit_from_version: int | None = Field(
        None, description="copy this version's config instead of a preset (R1.6)"
    )
    config: ModelConfig | None = None
    artifact_format: Literal["onnx", "pt", "torchscript", "safetensors", "tensorrt", "other"] = "onnx"
    training_run_id: str | None = None
    dataset_version: str | None = None
    metrics: dict[str, float] = Field(default_factory=dict)
    git_commit: str | None = None
    changelog: str = ""


class VersionPatch(BaseModel):
    config: ModelConfig | None = None
    training_run_id: str | None = None
    dataset_version: str | None = None
    metrics: dict[str, float] | None = None
    git_commit: str | None = None
    changelog: str | None = None


class ValidationOut(BaseModel):
    status: str = "pending"  # pending | running | passed | failed
    checked_at: datetime | None = None
    overlay_url: str | None = None
    sample_name: str | None = None
    timings: dict[str, float] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class VersionOut(ORMModel):
    id: str
    model_id: str
    version: int
    status: str
    preset: str | None
    artifact_format: str
    weights_bytes: int
    weights_sha256: str | None
    artifact_info: dict = Field(default_factory=dict)
    config: dict
    training_run_id: str | None
    dataset_version: str | None
    metrics: dict
    git_commit: str | None
    changelog: str
    source: str
    parent_version_id: str | None
    validation: dict
    created_by: str | None
    created_at: datetime
    published_at: datetime | None
    deprecated_at: datetime | None
    last_served_at: datetime | None
    tags: list[str] = Field(default_factory=list)
    deployment: "DeploymentOut | None" = None


class VersionCompare(BaseModel):
    """R2.5 - what the side-by-side view renders."""

    left: VersionOut
    right: VersionOut
    config_diff: list[dict[str, Any]]
    metric_diff: dict[str, dict[str, float | None]]
    traffic: dict[str, dict[str, float | None]]


# ---------------------------------------------------------------------- upload


class UploadInit(BaseModel):
    filename: str
    total_bytes: int = Field(ge=1)
    sha256: str | None = Field(None, min_length=64, max_length=64)
    chunk_bytes: int | None = Field(None, ge=5 * 1024 * 1024)

    @field_validator("filename")
    @classmethod
    def _safe(cls, v: str) -> str:
        if "/" in v or "\\" in v or v.startswith("."):
            raise ValueError("filename must not contain a path")
        return v


class UploadSessionOut(ORMModel):
    id: str
    filename: str
    total_bytes: int
    received_bytes: int
    chunk_bytes: int
    status: str
    next_part: int
    received_parts: list[int] = Field(default_factory=list)
    expires_at: datetime | None = None


# ------------------------------------------------------------------------ tags


class TagIn(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,62}$")
    version: int | None = None
    version_id: str | None = None


class TagPatch(BaseModel):
    version: int | None = None
    version_id: str | None = None
    locked: bool | None = None


# ------------------------------------------------------------------ deployment


class DeploymentIn(BaseModel):
    version: int | None = None
    version_id: str | None = None
    mode: Literal["primary", "canary", "shadow"] = "primary"
    traffic_weight: int = Field(100, ge=0, le=100)
    replicas: int = Field(1, ge=0, le=64)
    device: str = "auto"
    max_batch_size: int = Field(8, ge=1, le=128)
    max_wait_ms: int = Field(8, ge=0, le=2000)
    max_queue_depth: int = Field(64, ge=1, le=10000)
    request_timeout_ms: int = Field(30000, ge=100, le=600000)
    autoscale: bool = False
    min_replicas: int = Field(1, ge=0, le=64)
    max_replicas: int = Field(1, ge=1, le=64)
    target_queue_depth: int = Field(8, ge=1, le=1000)


class DeploymentPatch(BaseModel):
    replicas: int | None = Field(None, ge=0, le=64)
    device: str | None = None
    traffic_weight: int | None = Field(None, ge=0, le=100)
    max_batch_size: int | None = Field(None, ge=1, le=128)
    max_wait_ms: int | None = Field(None, ge=0, le=2000)
    max_queue_depth: int | None = Field(None, ge=1, le=10000)
    request_timeout_ms: int | None = Field(None, ge=100, le=600000)
    autoscale: bool | None = None
    min_replicas: int | None = Field(None, ge=0, le=64)
    max_replicas: int | None = Field(None, ge=1, le=64)
    target_queue_depth: int | None = Field(None, ge=1, le=1000)


class ReplicaOut(BaseModel):
    runtime_id: str
    state: str
    detail: str = ""
    device: str = ""
    gpu_name: str = ""
    queue_depth: int = 0
    loaded_at: datetime | None = None
    ready_at: datetime | None = None
    url: str = ""


class DeploymentOut(ORMModel):
    id: str
    model_id: str
    version_id: str
    version: int | None = None
    mode: str
    traffic_weight: int
    replicas: int
    device: str
    max_batch_size: int
    max_wait_ms: int
    max_queue_depth: int
    request_timeout_ms: int
    autoscale: bool
    min_replicas: int
    max_replicas: int
    target_queue_depth: int
    status: str
    status_detail: str
    created_at: datetime
    updated_at: datetime
    replicas_ready: int = 0
    replica_states: list[ReplicaOut] = Field(default_factory=list)


class RollbackIn(BaseModel):
    tag: str = "production"
    to_version: int | None = Field(
        None, description="defaults to the version the tag pointed at before its last move"
    )


# ------------------------------------------------------------------- inference


class PredictJSON(BaseModel):
    """JSON body form of a prediction request (R3.2)."""

    images: list[str] | None = Field(
        None, description="base64 payloads or data URLs, one per image"
    )
    image_urls: list[str] | None = Field(None, description="remote URLs the server fetches")
    image: str | None = Field(None, description="convenience single-image alias")
    image_url: str | None = None
    overrides: dict[str, Any] | None = Field(
        None, description="per-request threshold overrides (R3.6)"
    )
    inputs: dict[str, Any] | None = Field(
        None,
        description="named non-image inputs for a multi-input model, one row per image "
                    '(or a single row applied to all of them), e.g. {"aux": [0.5, 0.5, 0.2, 0.3]}',
    )
    extra: dict[str, Any] | None = Field(None, description="passed through to a hook")
    include_overlay: bool = False
    capture: bool | None = Field(None, description="force payload capture on or off")
    format: str | None = Field(
        None, description="'aperture' (default) or 'roboflow' for a Roboflow-shaped response"
    )


class AsyncJobIn(BaseModel):
    kind: Literal["bulk_images", "video"] = "bulk_images"
    model: str
    ref: str = "production"
    source_keys: list[str] | None = Field(None, description="object keys already in storage")
    source_urls: list[str] | None = None
    video_key: str | None = None
    frame_stride: int = Field(1, ge=1, le=1000)
    overrides: dict[str, Any] | None = None
    webhook_url: str | None = None


class JobOut(ORMModel):
    id: str
    kind: str
    status: str
    params: dict
    progress: int
    total: int
    result: dict
    error: str | None
    webhook_url: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


# --------------------------------------------------------------------- api keys


class ApiKeyIn(BaseModel):
    name: str
    scopes: list[str] = Field(default_factory=lambda: ["*"],
                              description='model ids/slugs, or ["*"] for every model')
    rate_limit_rpm: int = Field(600, ge=0, le=1_000_000)
    monthly_quota: int = Field(0, ge=0)
    expires_in_days: int | None = Field(None, ge=1, le=3650)


class ApiKeyPatch(BaseModel):
    name: str | None = None
    scopes: list[str] | None = None
    rate_limit_rpm: int | None = Field(None, ge=0, le=1_000_000)
    monthly_quota: int | None = Field(None, ge=0)


class ApiKeyOut(ORMModel):
    id: str
    name: str
    prefix: str
    scopes: list[str]
    rate_limit_rpm: int
    monthly_quota: int
    used_this_month: int = 0
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None


class ApiKeyCreated(ApiKeyOut):
    key: str = Field(description="shown once - Aperture stores only its hash")


# -------------------------------------------------------------- observability


class InferenceLogOut(ORMModel):
    id: str
    request_id: str
    trace_id: str | None
    model_id: str | None
    model_slug: str
    version_id: str | None
    version_number: int | None
    tag: str | None
    route: str
    caller_type: str
    caller_label: str
    status: str
    http_status: int
    error_code: str | None
    error_message: str | None
    batch_size: int
    queue_ms: float
    preprocess_ms: float
    inference_ms: float
    postprocess_ms: float
    gateway_ms: float
    total_ms: float
    runtime_id: str | None
    detection_count: int
    confidence: dict
    input_stats: dict
    input_ref: dict
    overrides: dict
    flagged: bool
    flag_label: str | None
    created_at: datetime
    has_capture: bool = False


class InferenceLogDetail(InferenceLogOut):
    output: dict
    capture: dict
    image_urls: list[str] = Field(default_factory=list)
    overlay_urls: list[str] = Field(default_factory=list)


class FlagIn(BaseModel):
    flagged: bool = True
    label: str | None = Field(None, description="e.g. false_positive, missed_detection, wrong_class")
    note: str | None = None


class TimeseriesPoint(BaseModel):
    ts: datetime
    requests: int = 0
    errors: int = 0
    p50_ms: float | None = None
    p95_ms: float | None = None
    p99_ms: float | None = None
    images: int = 0


class MetricsSummary(BaseModel):
    window_minutes: int
    requests: int
    errors: int
    error_rate: float
    images: int
    rps: float
    p50_ms: float | None
    p95_ms: float | None
    p99_ms: float | None
    stages: dict[str, float]
    by_status: dict[str, int]
    series: list[TimeseriesPoint]


class UsageRow(BaseModel):
    key: str
    label: str
    requests: int
    images: int
    errors: int
    error_rate: float
    p95_ms: float | None
    avg_ms: float | None


class ReportRequest(BaseModel):
    kind: Literal["usage", "errors", "latency", "flagged"] = "usage"
    group_by: Literal["model", "version", "consumer", "day", "error_code"] = "model"
    since_hours: int = Field(168, ge=1, le=24 * 400)
    model_id: str | None = None
    fmt: Literal["json", "csv", "pdf"] = "json"


class ReportScheduleIn(BaseModel):
    name: str
    kind: Literal["usage", "errors", "latency", "flagged"] = "usage"
    params: dict = Field(default_factory=dict)
    fmt: Literal["csv", "pdf"] = "csv"
    cron: str | None = Field(None, description="m h dom mon dow, evaluated in UTC")
    channel: Literal["none", "slack", "email", "webhook"] = "none"
    destination: str = ""
    enabled: bool = True


class AlertRuleIn(BaseModel):
    name: str
    metric: Literal["error_rate", "p95_latency_ms", "p99_latency_ms", "no_traffic",
                    "replica_down", "confidence_drop"]
    model_id: str | None = None
    version_id: str | None = None
    comparator: Literal[">", "<"] = ">"
    threshold: float = 0.0
    window_minutes: int = Field(5, ge=1, le=1440)
    min_samples: int = Field(20, ge=1, le=100000)
    channel: Literal["slack", "email", "webhook"] = "webhook"
    destination: str = ""
    enabled: bool = True


class AlertRuleOut(ORMModel):
    id: str
    name: str
    metric: str
    model_id: str | None
    version_id: str | None
    comparator: str
    threshold: float
    window_minutes: int
    min_samples: int
    channel: str
    destination: str
    enabled: bool
    state: str
    last_value: float | None
    last_fired_at: datetime | None
    last_checked_at: datetime | None


class DriftOut(BaseModel):
    version_id: str
    baseline: dict | None
    current: dict | None
    windows: list[dict]
    signals: list[dict]


class RuntimeOut(ORMModel):
    id: str
    url: str
    hostname: str
    device: str
    gpu_name: str
    total_memory_mb: int
    free_memory_mb: int
    slots: int
    status: str
    loaded: list
    metrics: dict
    last_heartbeat: datetime | None
    fresh: bool = True


class AuditOut(ORMModel):
    id: str
    project_id: str | None
    actor_type: str
    actor_id: str | None
    actor_label: str
    action: str
    target_type: str
    target_id: str | None
    target_label: str
    payload: dict
    client_ip: str | None
    created_at: datetime


class OverviewOut(BaseModel):
    project: ProjectOut
    models: int
    versions: int
    deployments: int
    replicas_ready: int
    replicas_desired: int
    runtimes: list[RuntimeOut]
    metrics: MetricsSummary
    recent_errors: list[InferenceLogOut]
    firing_alerts: list[AlertRuleOut]
    log_stream_depth: int


class PresetOut(BaseModel):
    name: str
    label: str
    family: str
    task: str
    description: str


class HookValidateIn(BaseModel):
    source: str
    kind: Literal["preprocess", "postprocess"] = "postprocess"


class HookValidateOut(BaseModel):
    ok: bool
    problems: list[str] = Field(default_factory=list)


# ------------------------------------------------------------------ workflows


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
    project_id: str
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


class RoboflowImportIn(BaseModel):
    """R1.11 - pull a trained model out of a Roboflow project."""

    ref: str = Field(
        description="a Roboflow model reference: 'workspace/project/version', or the "
                    "app URL, or the model_id a workflow names",
    )
    slug: str | None = Field(
        None, pattern=r"^[a-z0-9][a-z0-9._-]{1,94}$",
        description="the Aperture model to land it under; defaults to the project id. "
                    "An existing model gains a new version instead of being replaced.",
    )
    preset: str | None = Field(
        None, description="override the preset inferred from Roboflow's model type")
    api_key: str | None = Field(
        None, description="use this key instead of the instance's AP_ROBOFLOW_API_KEY")
    opset: int = Field(17, ge=11, le=21)
    changelog: str = ""


class ConversionIn(BaseModel):
    """R1.10 - queue an export/optimisation and land the result as a new version."""

    target: Literal["onnx", "onnx_fp16", "onnx_int8", "tensorrt_fp16", "tensorrt_int8"]
    opset: int = Field(17, ge=11, le=21)
    calibration_keys: list[str] = Field(default_factory=list)
    workspace_mb: int = Field(4096, ge=256, le=65536)
    changelog: str = ""


TokenResponse.model_rebuild()
VersionOut.model_rebuild()
