"""The metadata plane. Weights live in object storage; everything else lives here."""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JSON_T = JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def new_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(),
        nullable=False,
    )


# --------------------------------------------------------------------- tenancy


class Project(Base, TimestampMixin):
    """R7.3 - the isolation unit for models, keys, logs and quotas."""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_id)
    slug: Mapped[str] = mapped_column(sa.String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(sa.String(160), nullable=False)
    description: Mapped[str] = mapped_column(sa.Text, default="")
    capture_sample_rate: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    capture_retention_days: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    # passive_deletes lets Postgres' ON DELETE CASCADE do the work; without it
    # SQLAlchemy tries to NULL the child FK first, which the schema forbids.
    models: Mapped[list["Model"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(sa.String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(sa.String(160), default="")
    password_hash: Mapped[str | None] = mapped_column(sa.String(255))
    provider: Mapped[str] = mapped_column(sa.String(32), default="local")  # local|oidc|ldap
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    is_superadmin: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class Membership(Base, TimestampMixin):
    """R7.2 - RBAC is per project: viewer | developer | admin."""

    __tablename__ = "memberships"
    __table_args__ = (sa.UniqueConstraint("user_id", "project_id", name="uq_member_user_project"),)

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(
        sa.ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(sa.String(16), default="viewer")


class ApiKey(Base, TimestampMixin):
    """R7.1 - scoped, revocable, never stored in the clear."""

    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        sa.ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(sa.String(160), nullable=False)
    prefix: Mapped[str] = mapped_column(sa.String(24), unique=True, index=True)
    key_hash: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    scopes: Mapped[list] = mapped_column(JSON_T, default=list)  # ["*"] or model ids
    rate_limit_rpm: Mapped[int] = mapped_column(sa.Integer, default=600)
    monthly_quota: Mapped[int] = mapped_column(sa.Integer, default=0)
    created_by: Mapped[str | None] = mapped_column(sa.String(36))
    last_used_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class PersonalAccessToken(Base, TimestampMixin):
    """R8.1 - a long-lived credential that *is* the user.

    Issued for agents and scripts that outlive a browser session. It carries no
    scopes and no rate limit: it inherits the owner's project roles, and the
    owner can revoke it at any time. Like an API key it is stored only as a hash.
    """

    __tablename__ = "personal_access_tokens"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(sa.String(160), nullable=False)
    prefix: Mapped[str] = mapped_column(sa.String(24), unique=True, index=True)
    token_hash: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


# ---------------------------------------------------------------- registry


class Model(Base, TimestampMixin):
    __tablename__ = "models"
    __table_args__ = (sa.UniqueConstraint("project_id", "slug", name="uq_model_project_slug"),)

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        sa.ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    slug: Mapped[str] = mapped_column(sa.String(96), nullable=False, index=True)
    name: Mapped[str] = mapped_column(sa.String(160), nullable=False)
    task: Mapped[str] = mapped_column(sa.String(32), nullable=False, index=True)
    owner: Mapped[str] = mapped_column(sa.String(160), default="")
    framework: Mapped[str] = mapped_column(sa.String(32), default="onnx")
    description: Mapped[str] = mapped_column(sa.Text, default="")
    labels: Mapped[list] = mapped_column(JSON_T, default=list)
    archived_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    project: Mapped[Project] = relationship(back_populates="models")
    versions: Mapped[list["ModelVersion"]] = relationship(
        back_populates="model", order_by="desc(ModelVersion.version)",
        cascade="all, delete-orphan", passive_deletes=True,
    )
    tags: Mapped[list["Tag"]] = relationship(
        back_populates="model", cascade="all, delete-orphan", passive_deletes=True
    )


class ModelVersion(Base, TimestampMixin):
    """R2.1 - immutable once published. weights_key is never rewritten."""

    __tablename__ = "model_versions"
    __table_args__ = (sa.UniqueConstraint("model_id", "version", name="uq_version_model_number"),)

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_id)
    model_id: Mapped[str] = mapped_column(
        sa.ForeignKey("models.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    status: Mapped[str] = mapped_column(sa.String(24), default="draft", index=True)
    # draft -> validating -> ready | failed ; ready -> deprecated -> archived

    config: Mapped[dict] = mapped_column(JSON_T, default=dict)
    preset: Mapped[str | None] = mapped_column(sa.String(48))
    artifact_format: Mapped[str] = mapped_column(sa.String(24), default="onnx")
    weights_key: Mapped[str | None] = mapped_column(sa.String(512))
    weights_bytes: Mapped[int] = mapped_column(sa.BigInteger, default=0)
    weights_sha256: Mapped[str | None] = mapped_column(sa.String(64), index=True)
    extra_files: Mapped[dict] = mapped_column(JSON_T, default=dict)  # e.g. torchscript deps
    # what the uploaded bytes actually are, decided at upload time: a .pt can be
    # TorchScript (servable), an Ultralytics checkpoint (convertible) or a bare
    # state_dict (nothing can execute it). See aperture_core.artifacts.
    artifact_info: Mapped[dict] = mapped_column(JSON_T, default=dict)

    # R2.4 - provenance
    training_run_id: Mapped[str | None] = mapped_column(sa.String(160))
    dataset_version: Mapped[str | None] = mapped_column(sa.String(160))
    metrics: Mapped[dict] = mapped_column(JSON_T, default=dict)  # {"mAP50": .., "f1": ..}
    git_commit: Mapped[str | None] = mapped_column(sa.String(64))
    changelog: Mapped[str] = mapped_column(sa.Text, default="")
    source: Mapped[str] = mapped_column(sa.String(24), default="upload")
    parent_version_id: Mapped[str | None] = mapped_column(sa.String(36))

    validation: Mapped[dict] = mapped_column(JSON_T, default=dict)  # R1.7 result + overlay key
    created_by: Mapped[str | None] = mapped_column(sa.String(36))
    published_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    deprecated_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_served_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    model: Mapped[Model] = relationship(back_populates="versions")


class UploadSession(Base, TimestampMixin):
    """R1.1 - resumable multipart upload for multi-GB weights."""

    __tablename__ = "upload_sessions"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(sa.String(36), index=True)
    model_id: Mapped[str | None] = mapped_column(sa.String(36), index=True)
    filename: Mapped[str] = mapped_column(sa.String(320))
    total_bytes: Mapped[int] = mapped_column(sa.BigInteger, default=0)
    received_bytes: Mapped[int] = mapped_column(sa.BigInteger, default=0)
    chunk_bytes: Mapped[int] = mapped_column(sa.Integer, default=32 * 1024 * 1024)
    key: Mapped[str] = mapped_column(sa.String(512))
    multipart_id: Mapped[str | None] = mapped_column(sa.String(320))
    parts: Mapped[list] = mapped_column(JSON_T, default=list)  # [{part, etag, size, sha256}]
    sha256_expected: Mapped[str | None] = mapped_column(sa.String(64))
    status: Mapped[str] = mapped_column(sa.String(24), default="open")
    created_by: Mapped[str | None] = mapped_column(sa.String(36))
    expires_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class Tag(Base, TimestampMixin):
    """R2.2/R2.3 - a movable pointer. Clients call the tag, we resolve it per request."""

    __tablename__ = "tags"
    __table_args__ = (sa.UniqueConstraint("model_id", "name", name="uq_tag_model_name"),)

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_id)
    model_id: Mapped[str] = mapped_column(
        sa.ForeignKey("models.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    # cascade so deleting a model cannot leave a tag pointing at a deleted version
    version_id: Mapped[str] = mapped_column(
        sa.ForeignKey("model_versions.id", ondelete="CASCADE"), nullable=False
    )
    previous_version_id: Mapped[str | None] = mapped_column(sa.String(36))
    updated_by: Mapped[str | None] = mapped_column(sa.String(36))
    locked: Mapped[bool] = mapped_column(sa.Boolean, default=False)

    model: Mapped[Model] = relationship(back_populates="tags")


# ---------------------------------------------------------------- serving


class Deployment(Base, TimestampMixin):
    """Desired serving state for one version. The reconciler turns it into assignments."""

    __tablename__ = "deployments"
    __table_args__ = (sa.UniqueConstraint("version_id", "mode", name="uq_deploy_version_mode"),)

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(sa.String(36), index=True)
    model_id: Mapped[str] = mapped_column(
        sa.ForeignKey("models.id", ondelete="CASCADE"), index=True
    )
    version_id: Mapped[str] = mapped_column(
        sa.ForeignKey("model_versions.id", ondelete="CASCADE"), index=True
    )
    mode: Mapped[str] = mapped_column(sa.String(16), default="primary")  # primary|canary|shadow
    traffic_weight: Mapped[int] = mapped_column(sa.Integer, default=100)  # R2.7
    replicas: Mapped[int] = mapped_column(sa.Integer, default=1)
    device: Mapped[str] = mapped_column(sa.String(24), default="auto")  # auto|cpu|cuda:0
    max_batch_size: Mapped[int] = mapped_column(sa.Integer, default=8)   # R4.2
    max_wait_ms: Mapped[int] = mapped_column(sa.Integer, default=8)
    max_queue_depth: Mapped[int] = mapped_column(sa.Integer, default=64)  # R4.3
    request_timeout_ms: Mapped[int] = mapped_column(sa.Integer, default=30_000)
    min_replicas: Mapped[int] = mapped_column(sa.Integer, default=1)      # R4.8
    max_replicas: Mapped[int] = mapped_column(sa.Integer, default=1)
    autoscale: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    target_queue_depth: Mapped[int] = mapped_column(sa.Integer, default=8)
    status: Mapped[str] = mapped_column(sa.String(24), default="pending", index=True)
    # pending -> warming -> live -> draining -> stopped | failed
    status_detail: Mapped[str] = mapped_column(sa.Text, default="")
    stopped_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class Runtime(Base, TimestampMixin):
    """A serving process that heartbeats in and is handed assignments."""

    __tablename__ = "runtimes"

    id: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    url: Mapped[str] = mapped_column(sa.String(320), nullable=False)
    hostname: Mapped[str] = mapped_column(sa.String(160), default="")
    device: Mapped[str] = mapped_column(sa.String(24), default="cpu")
    gpu_name: Mapped[str] = mapped_column(sa.String(160), default="")
    total_memory_mb: Mapped[int] = mapped_column(sa.Integer, default=0)
    free_memory_mb: Mapped[int] = mapped_column(sa.Integer, default=0)
    slots: Mapped[int] = mapped_column(sa.Integer, default=4)
    build: Mapped[str] = mapped_column(sa.String(64), default="")
    status: Mapped[str] = mapped_column(sa.String(24), default="ready", index=True)
    loaded: Mapped[list] = mapped_column(JSON_T, default=list)   # [{version_id, state, ...}]
    metrics: Mapped[dict] = mapped_column(JSON_T, default=dict)
    last_heartbeat: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), index=True
    )


class Assignment(Base, TimestampMixin):
    """Which runtime should hold which version - one row per replica."""

    __tablename__ = "assignments"
    __table_args__ = (
        sa.UniqueConstraint("runtime_id", "version_id", name="uq_assign_runtime_version"),
    )

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_id)
    deployment_id: Mapped[str] = mapped_column(
        sa.ForeignKey("deployments.id", ondelete="CASCADE"), index=True
    )
    runtime_id: Mapped[str] = mapped_column(sa.String(64), index=True)
    version_id: Mapped[str] = mapped_column(sa.String(36), index=True)
    state: Mapped[str] = mapped_column(sa.String(24), default="pending")
    # pending -> loading -> warming -> ready | failed ; draining
    detail: Mapped[str] = mapped_column(sa.Text, default="")
    ready_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


# ---------------------------------------------------------- observability


class InferenceLog(Base):
    """R5.1 - one row per inference request. Written asynchronously off the hot path."""

    __tablename__ = "inference_logs"
    __table_args__ = (
        sa.Index("ix_inflog_model_created", "model_id", "created_at"),
        sa.Index("ix_inflog_project_created", "project_id", "created_at"),
        sa.Index("ix_inflog_status_created", "status", "created_at"),
        sa.Index("ix_inflog_version_created", "version_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_id)
    request_id: Mapped[str] = mapped_column(sa.String(64), index=True)
    trace_id: Mapped[str | None] = mapped_column(sa.String(64), index=True)
    span_id: Mapped[str | None] = mapped_column(sa.String(32))

    project_id: Mapped[str] = mapped_column(sa.String(36), index=True)
    model_id: Mapped[str | None] = mapped_column(sa.String(36))
    version_id: Mapped[str | None] = mapped_column(sa.String(36))
    model_slug: Mapped[str] = mapped_column(sa.String(96), default="")
    version_number: Mapped[int | None] = mapped_column(sa.Integer)
    tag: Mapped[str | None] = mapped_column(sa.String(64))
    route: Mapped[str] = mapped_column(sa.String(24), default="primary")  # primary|canary|shadow

    caller_type: Mapped[str] = mapped_column(sa.String(16), default="api_key")
    caller_id: Mapped[str | None] = mapped_column(sa.String(36), index=True)
    caller_label: Mapped[str] = mapped_column(sa.String(160), default="")
    client_ip: Mapped[str | None] = mapped_column(sa.String(64))

    status: Mapped[str] = mapped_column(sa.String(16), default="ok")  # ok|error|timeout|rejected
    http_status: Mapped[int] = mapped_column(sa.Integer, default=200)
    error_code: Mapped[str | None] = mapped_column(sa.String(64))
    error_message: Mapped[str | None] = mapped_column(sa.Text)

    batch_size: Mapped[int] = mapped_column(sa.Integer, default=1)
    queue_ms: Mapped[float] = mapped_column(sa.Float, default=0.0)
    preprocess_ms: Mapped[float] = mapped_column(sa.Float, default=0.0)
    inference_ms: Mapped[float] = mapped_column(sa.Float, default=0.0)
    postprocess_ms: Mapped[float] = mapped_column(sa.Float, default=0.0)
    gateway_ms: Mapped[float] = mapped_column(sa.Float, default=0.0)
    total_ms: Mapped[float] = mapped_column(sa.Float, default=0.0, index=True)

    runtime_id: Mapped[str | None] = mapped_column(sa.String(64))
    input_ref: Mapped[dict] = mapped_column(JSON_T, default=dict)   # {source, names, bytes, sha256}
    input_stats: Mapped[dict] = mapped_column(JSON_T, default=dict)  # R5.9
    output: Mapped[dict] = mapped_column(JSON_T, default=dict)       # truncated predictions
    confidence: Mapped[dict] = mapped_column(JSON_T, default=dict)
    overrides: Mapped[dict] = mapped_column(JSON_T, default=dict)
    capture: Mapped[dict] = mapped_column(JSON_T, default=dict)      # {images: [keys], overlay: key}
    detection_count: Mapped[int] = mapped_column(sa.Integer, default=0)

    flagged: Mapped[bool] = mapped_column(sa.Boolean, default=False, index=True)
    flag_label: Mapped[str | None] = mapped_column(sa.String(64))
    flag_note: Mapped[str | None] = mapped_column(sa.Text)
    flagged_by: Mapped[str | None] = mapped_column(sa.String(36))

    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True
    )
    purge_after: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), index=True)


class Job(Base, TimestampMixin):
    """R3.8 / R1.10 - async work: bulk + video inference, conversion, exports, reports."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(sa.String(36), index=True)
    kind: Mapped[str] = mapped_column(sa.String(48), index=True)
    status: Mapped[str] = mapped_column(sa.String(24), default="queued", index=True)
    # queued -> running -> succeeded | failed | cancelled
    params: Mapped[dict] = mapped_column(JSON_T, default=dict)
    progress: Mapped[int] = mapped_column(sa.Integer, default=0)
    total: Mapped[int] = mapped_column(sa.Integer, default=0)
    result: Mapped[dict] = mapped_column(JSON_T, default=dict)
    error: Mapped[str | None] = mapped_column(sa.Text)
    webhook_url: Mapped[str | None] = mapped_column(sa.String(512))
    created_by: Mapped[str | None] = mapped_column(sa.String(36))
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class AuditLog(Base):
    """R7.4 - append-only record of every mutating action."""

    __tablename__ = "audit_logs"
    __table_args__ = (sa.Index("ix_audit_project_created", "project_id", "created_at"),)

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_id)
    project_id: Mapped[str | None] = mapped_column(sa.String(36), index=True)
    actor_type: Mapped[str] = mapped_column(sa.String(16), default="user")
    actor_id: Mapped[str | None] = mapped_column(sa.String(36))
    actor_label: Mapped[str] = mapped_column(sa.String(200), default="")
    action: Mapped[str] = mapped_column(sa.String(64), index=True)
    target_type: Mapped[str] = mapped_column(sa.String(48), default="")
    target_id: Mapped[str | None] = mapped_column(sa.String(96))
    target_label: Mapped[str] = mapped_column(sa.String(200), default="")
    payload: Mapped[dict] = mapped_column(JSON_T, default=dict)
    client_ip: Mapped[str | None] = mapped_column(sa.String(64))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True
    )


class AlertRule(Base, TimestampMixin):
    """R5.8 - evaluated every minute by the scheduled worker."""

    __tablename__ = "alert_rules"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(sa.String(36), index=True)
    name: Mapped[str] = mapped_column(sa.String(160))
    metric: Mapped[str] = mapped_column(sa.String(32))
    # error_rate | p99_latency_ms | p95_latency_ms | no_traffic | replica_down | confidence_drop
    model_id: Mapped[str | None] = mapped_column(sa.String(36))
    version_id: Mapped[str | None] = mapped_column(sa.String(36))
    comparator: Mapped[str] = mapped_column(sa.String(4), default=">")
    threshold: Mapped[float] = mapped_column(sa.Float, default=0.0)
    window_minutes: Mapped[int] = mapped_column(sa.Integer, default=5)
    min_samples: Mapped[int] = mapped_column(sa.Integer, default=20)
    channel: Mapped[str] = mapped_column(sa.String(16), default="webhook")  # slack|email|webhook
    destination: Mapped[str] = mapped_column(sa.String(512), default="")
    enabled: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    state: Mapped[str] = mapped_column(sa.String(16), default="ok")  # ok|firing
    last_value: Mapped[float | None] = mapped_column(sa.Float)
    last_fired_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_checked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class AlertEvent(Base):
    __tablename__ = "alert_events"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_id)
    rule_id: Mapped[str] = mapped_column(sa.ForeignKey("alert_rules.id", ondelete="CASCADE"),
                                        index=True)
    project_id: Mapped[str] = mapped_column(sa.String(36), index=True)
    value: Mapped[float] = mapped_column(sa.Float, default=0.0)
    message: Mapped[str] = mapped_column(sa.Text, default="")
    delivered: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    delivery_error: Mapped[str | None] = mapped_column(sa.Text)
    resolved_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True
    )


class DriftWindow(Base):
    """R5.9 - hourly rollup of confidence, class mix and input statistics."""

    __tablename__ = "drift_windows"
    __table_args__ = (
        sa.UniqueConstraint("version_id", "window_start", name="uq_drift_version_window"),
    )

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(sa.String(36), index=True)
    model_id: Mapped[str] = mapped_column(sa.String(36), index=True)
    version_id: Mapped[str] = mapped_column(sa.String(36), index=True)
    window_start: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), index=True)
    window_end: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    request_count: Mapped[int] = mapped_column(sa.Integer, default=0)
    stats: Mapped[dict] = mapped_column(JSON_T, default=dict)
    is_baseline: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


class Workflow(Base, TimestampMixin):
    """A DAG of model calls and transforms, exposed as one endpoint.

    The graph is a plain JSON document ({nodes, edges}) so the visual editor and
    the execution engine share one representation - what you drew is literally
    what runs. `revision` increments on publish, and every run records the
    revision it executed, which is what makes a result traceable back to a
    specific graph.
    """

    __tablename__ = "workflows"
    __table_args__ = (sa.UniqueConstraint("project_id", "slug", name="uq_workflow_project_slug"),)

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        sa.ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    slug: Mapped[str] = mapped_column(sa.String(96), nullable=False, index=True)
    name: Mapped[str] = mapped_column(sa.String(160), nullable=False)
    description: Mapped[str] = mapped_column(sa.Text, default="")
    owner: Mapped[str] = mapped_column(sa.String(160), default="")
    graph: Mapped[dict] = mapped_column(JSON_T, default=dict)          # the draft
    published_graph: Mapped[dict | None] = mapped_column(JSON_T)        # what runs
    revision: Mapped[int] = mapped_column(sa.Integer, default=0)
    status: Mapped[str] = mapped_column(sa.String(16), default="draft")  # draft|published
    labels: Mapped[list] = mapped_column(JSON_T, default=list)
    created_by: Mapped[str | None] = mapped_column(sa.String(36))
    published_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class WorkflowRun(Base):
    """One execution, with per-node timings and status."""

    __tablename__ = "workflow_runs"
    __table_args__ = (sa.Index("ix_wfrun_workflow_created", "workflow_id", "created_at"),)

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_id)
    workflow_id: Mapped[str] = mapped_column(
        sa.ForeignKey("workflows.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[str] = mapped_column(sa.String(36), index=True)
    request_id: Mapped[str] = mapped_column(sa.String(64), index=True)
    trace_id: Mapped[str | None] = mapped_column(sa.String(64))
    revision: Mapped[int] = mapped_column(sa.Integer, default=0)
    status: Mapped[str] = mapped_column(sa.String(16), default="ok")  # ok|error|partial
    error_code: Mapped[str | None] = mapped_column(sa.String(64))
    error_message: Mapped[str | None] = mapped_column(sa.Text)
    failed_node: Mapped[str | None] = mapped_column(sa.String(64))
    caller_type: Mapped[str] = mapped_column(sa.String(16), default="api_key")
    caller_id: Mapped[str | None] = mapped_column(sa.String(36))
    caller_label: Mapped[str] = mapped_column(sa.String(160), default="")
    image_count: Mapped[int] = mapped_column(sa.Integer, default=0)
    model_calls: Mapped[int] = mapped_column(sa.Integer, default=0)
    total_ms: Mapped[float] = mapped_column(sa.Float, default=0.0, index=True)
    node_stats: Mapped[list] = mapped_column(JSON_T, default=list)
    output: Mapped[dict] = mapped_column(JSON_T, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True
    )


class ReportSchedule(Base, TimestampMixin):
    """R5.7 - a saved report definition plus an optional cron for delivery."""

    __tablename__ = "report_schedules"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(sa.String(36), index=True)
    name: Mapped[str] = mapped_column(sa.String(160))
    kind: Mapped[str] = mapped_column(sa.String(32), default="usage")
    params: Mapped[dict] = mapped_column(JSON_T, default=dict)
    fmt: Mapped[str] = mapped_column(sa.String(8), default="csv")  # csv|pdf
    cron: Mapped[str | None] = mapped_column(sa.String(64))  # m h dom mon dow
    channel: Mapped[str] = mapped_column(sa.String(16), default="none")
    destination: Mapped[str] = mapped_column(sa.String(512), default="")
    enabled: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_artifact_key: Mapped[str | None] = mapped_column(sa.String(512))
    created_by: Mapped[str | None] = mapped_column(sa.String(36))
