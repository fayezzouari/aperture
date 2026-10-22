"""Where the server points and who it is. Everything comes from the environment.

    APERTURE_URL      https://aperture.internal      (required)
    APERTURE_TOKEN    ap_pat_...                     (required)
    APERTURE_WORKSPACE cloud                         (optional workspace slug)

The token decides the mode. A personal access token (`ap_pat_`) is a user and
reaches everything the user can; an API key (`ap_`) only reaches the inference
routes, so the server registers just `predict` and `run_workflow` for it rather
than offering twenty tools that would all answer 401.

Without APERTURE_WORKSPACE a user's calls act in the workspace they switched
to last in the console. An API key acts in the workspace it belongs to, and
naming any other one is refused.

The shared HTTP server (`aperture-mcp --http`) holds no token of its own: each
request carries its caller's, so `token` is empty there and `active_token`
reads the caller bound for the request. It takes instead:

    APERTURE_URL                 http://api:8000     (required; the API as this host sees it)
    APERTURE_PUBLIC_URL          https://aperture.internal   (what people open; used in hints)
    APERTURE_MCP_HOST            0.0.0.0
    APERTURE_MCP_PORT            8090
    APERTURE_S3_PUBLIC_ENDPOINT  http://localhost:9000   (optional, see below)
    APERTURE_S3_ENDPOINT         http://minio:9000

Captures come back as presigned URLs on the object store's public endpoint,
which a container may not be able to reach (`localhost:9000` is the container
itself). With both S3 variables set, those URLs are fetched from the internal
endpoint instead, keeping the public Host header the signature was made for.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from . import caller

PAT_PREFIX = "ap_pat_"
API_KEY_PREFIX = "ap_"


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    url: str
    token: str
    workspace: str | None = None
    timeout_s: float = 60.0
    predict_timeout_s: float = 180.0
    max_image_px: int = 1568  # longest side handed back to the model; larger buys nothing
    max_images: int = 8
    max_upload_bytes: int = 24 * 1024 * 1024
    remote: bool = False  # shared over HTTP: per-request callers, no local files
    public_url: str | None = None
    s3_public_endpoint: str | None = None
    s3_endpoint: str | None = None
    host: str = "0.0.0.0"
    port: int = 8090

    @property
    def active_token(self) -> str:
        if self.remote:
            bound = caller.current()
            return bound.token if bound else ""
        return self.token

    @property
    def active_workspace(self) -> str | None:
        if self.remote:
            bound = caller.current()
            return bound.workspace if bound else None
        return self.workspace

    @property
    def mode(self) -> str:
        return mode_of(self.active_token)

    @property
    def console_url(self) -> str:
        """Where a person creates a token: the public URL, not the API's internal one."""
        return self.public_url or self.url


def mode_of(token: str) -> str:
    if token.startswith(PAT_PREFIX):
        return "pat"
    if token.startswith(API_KEY_PREFIX):
        return "api_key"
    return "session"


def _url(env: Mapping[str, str], name: str) -> str | None:
    value = (env.get(name) or "").strip().rstrip("/")
    if value and not value.startswith(("http://", "https://")):
        raise ConfigError(f"{name} must start with http:// or https://, got {value!r}")
    return value or None


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    url = (env.get("APERTURE_URL") or "").strip().rstrip("/")
    token = (env.get("APERTURE_TOKEN") or "").strip()
    if not url:
        raise ConfigError("APERTURE_URL is not set (e.g. http://localhost:8080)")
    if not url.startswith(("http://", "https://")):
        raise ConfigError(f"APERTURE_URL must start with http:// or https://, got {url!r}")
    if not token:
        raise ConfigError(
            "APERTURE_TOKEN is not set - create a personal access token under "
            f"{url}/account/tokens"
        )
    workspace = (env.get("APERTURE_WORKSPACE") or "").strip() or None
    if workspace and workspace.startswith("${"):
        workspace = None  # an optional field a desktop extension left unsubstituted
    return Settings(url=url, token=token, workspace=workspace)


def load_remote_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    url = _url(env, "APERTURE_URL")
    if not url:
        raise ConfigError("APERTURE_URL is not set (the API as this host reaches it, "
                          "e.g. http://api:8000)")
    try:
        port = int(env.get("APERTURE_MCP_PORT") or 8090)
    except ValueError as exc:
        raise ConfigError(f"APERTURE_MCP_PORT must be a number, got "
                          f"{env.get('APERTURE_MCP_PORT')!r}") from exc
    public_s3, internal_s3 = _url(env, "APERTURE_S3_PUBLIC_ENDPOINT"), _url(env, "APERTURE_S3_ENDPOINT")
    if bool(public_s3) != bool(internal_s3):
        raise ConfigError("set both APERTURE_S3_PUBLIC_ENDPOINT and APERTURE_S3_ENDPOINT, or neither")
    return Settings(url=url, token="", remote=True,
                    public_url=_url(env, "APERTURE_PUBLIC_URL"),
                    s3_public_endpoint=public_s3, s3_endpoint=internal_s3,
                    host=(env.get("APERTURE_MCP_HOST") or "0.0.0.0").strip(), port=port)
