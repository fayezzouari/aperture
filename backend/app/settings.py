"""All configuration in one place, env-driven, no outbound defaults (R7.7)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AP_", env_file=".env", extra="ignore")

    # ------------------------------------------------------------------ identity
    env: str = "dev"
    instance_name: str = "Aperture"
    public_url: str = "http://localhost:8080"

    # ------------------------------------------------------------------ storage
    database_url: str = "postgresql+asyncpg://aperture:aperture@postgres:5432/aperture"
    redis_url: str = "redis://redis:6379/0"

    s3_endpoint: str = "http://minio:9000"
    s3_public_endpoint: str = "http://localhost:9000"
    s3_region: str = "us-east-1"
    s3_access_key: str = "aperture"
    s3_secret_key: str = "aperture-secret"
    s3_bucket_weights: str = "aperture-weights"
    s3_bucket_captures: str = "aperture-captures"
    s3_bucket_reports: str = "aperture-reports"
    s3_force_path_style: bool = True
    # R7.5 - server-side encryption for weights and captured images. Empty by
    # default: MinIO refuses SSE-S3 unless a KMS (KES) is configured, and a
    # default that breaks every upload is worse than none. Set to "AES256"
    # against AWS S3 or a KMS-backed MinIO; on plain MinIO, encrypt the volume.
    s3_sse: str = ""

    # ------------------------------------------------------------------ security
    jwt_secret: str = "change-me-in-production"
    jwt_ttl_minutes: int = 720
    bootstrap_admin_email: str = "admin@localhost"
    bootstrap_admin_password: str = "aperture"
    allow_password_login: bool = True

    oidc_enabled: bool = False
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_scopes: str = "openid email profile groups"
    oidc_admin_groups: str = "aperture-admins"
    oidc_developer_groups: str = "aperture-developers"

    ldap_enabled: bool = False
    ldap_url: str = ""
    ldap_bind_dn: str = ""
    ldap_bind_password: str = ""
    ldap_user_base_dn: str = ""
    ldap_user_filter: str = "(uid={username})"
    ldap_admin_group: str = ""

    cors_origins: str = "http://localhost:5173,http://localhost:8080"

    # ------------------------------------------------------------------ serving
    runtime_internal_token: str = "change-me-runtime-token"
    max_upload_mb: int = 8192
    upload_chunk_mb: int = 32
    max_payload_mb: int = 32
    max_batch_images: int = 64
    # "aperture" or "roboflow" - the response shape when a caller does not ask
    # for one explicitly. Set to roboflow to drop in behind existing clients.
    default_response_format: str = "aperture"
    default_request_timeout_ms: int = 30_000
    gateway_connect_timeout_ms: int = 2_000
    allow_remote_image_urls: bool = True
    remote_url_allowlist: str = ""  # empty = any host; set to lock down SSRF surface
    runtime_heartbeat_ttl_s: int = 20

    # ------------------------------------------------------------- roboflow
    # Used to import a trained model straight from a Roboflow project: read the
    # version's class list and download its weights. Only ever sent to Roboflow.
    # An import request may carry its own key instead, which wins over this one.
    roboflow_api_key: str = ""

    # ------------------------------------------------- roboflow compatibility
    # POST /infer/workflows/{workspace}/{workflow} answers in Roboflow's shape so
    # a client already speaking to Roboflow moves over by changing a base URL.
    # Off by default: it is a migration aid, not part of the API.
    roboflow_compat_enabled: bool = False
    # Which Aperture models stand in for each workflow, as JSON. A workflow is
    # matched by prefix, because the same workflow carries an environment suffix
    # in every deployment ("cells-detection-production" / "-staging").
    #   {"<workflow-id-prefix>": {"kind": "fanout"|"fixed",
    #                             "tag": "development",
    #                             "models": {"<output key>": "<slug>[@<tag>]"}}}
    # "fanout" picks its models from the request's `part_names`; "fixed" runs
    # every model listed. "@tag" pins one model to its own tag instead of the
    # workflow's. An empty string keeps the built-in defaults.
    roboflow_compat_workflows: str = ""

    # ------------------------------------------------------------ observability
    capture_sample_rate: float = 0.05
    capture_errors_always: bool = True
    capture_retention_days: int = 30
    log_output_max_bytes: int = 32_768
    otel_enabled: bool = False
    otel_endpoint: str = "http://otel-collector:4318"
    log_level: str = "INFO"

    # ------------------------------------------------------------------ limits
    smtp_host: str = ""
    smtp_port: int = 25
    smtp_from: str = "aperture@localhost"

    default_rate_limit_rpm: int = 600
    default_monthly_quota: int = 0  # 0 = unlimited

    @field_validator("capture_sample_rate")
    @classmethod
    def _rate(cls, v: float) -> float:
        return min(max(v, 0.0), 1.0)

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def url_allowlist(self) -> list[str]:
        return [h.strip().lower() for h in self.remote_url_allowlist.split(",") if h.strip()]

    @property
    def oidc_scope_list(self) -> list[str]:
        return [s for s in self.oidc_scopes.split() if s]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
