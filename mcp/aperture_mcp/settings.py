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
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

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

    @property
    def mode(self) -> str:
        if self.token.startswith(PAT_PREFIX):
            return "pat"
        if self.token.startswith(API_KEY_PREFIX):
            return "api_key"
        return "session"


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
    return Settings(url=url, token=token, workspace=workspace)
