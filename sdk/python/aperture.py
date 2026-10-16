"""Minimal Aperture client. No dependencies beyond httpx.

This is deliberately small and hand-written so it can be vendored into a product
without a code-generation step. For a fully typed client generated from the
OpenAPI document, run `make sdk`.

    from aperture import Aperture

    client = Aperture("https://aperture.internal", api_key=os.environ["APERTURE_KEY"])
    result = client.predict("part-detection", "production", images=["damage.jpg"])
    for detection in result.detections(0):
        print(detection["class_name"], detection["score"], detection["box"])
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Any, BinaryIO, Iterable, Sequence

import httpx

__all__ = ["Aperture", "ApertureError", "Prediction"]


class ApertureError(RuntimeError):
    """Carries Aperture's machine-readable error contract."""

    def __init__(self, status: int, code: str, message: str, request_id: str | None = None,
                 details: dict | None = None):
        self.status = status
        self.code = code
        self.request_id = request_id
        self.details = details or {}
        super().__init__(f"{code}: {message}" + (f" (request {request_id})" if request_id else ""))

    @property
    def retryable(self) -> bool:
        """queue_full and rate_limited mean 'try again shortly', not 'give up'."""
        return self.code in {"queue_full", "rate_limited", "no_replica_available",
                             "runtime_unhealthy", "upstream_error", "timeout"}


@dataclass
class Prediction:
    raw: dict[str, Any]

    @property
    def request_id(self) -> str:
        return self.raw["request_id"]

    @property
    def model_version(self) -> int:
        """The version that actually served this call, even when a tag was used."""
        return self.raw["model_version"]

    @property
    def latency_ms(self) -> float:
        return self.raw["latency"]["total_ms"]

    @property
    def results(self) -> list[dict]:
        return self.raw["results"]

    def detections(self, index: int = 0) -> list[dict]:
        return self.results[index].get("detections") or []

    def classifications(self, index: int = 0) -> list[dict]:
        return self.results[index].get("classifications") or []

    def texts(self, index: int = 0) -> list[dict]:
        return self.results[index].get("texts") or []

    def embedding(self, index: int = 0) -> list[float] | None:
        return self.results[index].get("embedding")


class Aperture:
    def __init__(self, base_url: str, *, api_key: str | None = None,
                 timeout: float = 30.0, client: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.environ.get("APERTURE_KEY", "")
        self._client = client or httpx.Client(timeout=timeout)

    # ------------------------------------------------------------------ helpers

    def _headers(self, extra: dict | None = None) -> dict[str, str]:
        headers = {}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        headers.update(extra or {})
        return headers

    def _raise(self, response: httpx.Response) -> None:
        body: dict = {}
        try:
            body = response.json()
        except ValueError:
            pass
        error = body.get("error", {})
        raise ApertureError(
            response.status_code,
            error.get("code", "internal_error"),
            error.get("message", response.text[:200]),
            error.get("request_id") or response.headers.get("x-request-id"),
            error.get("details"),
        )

    # ---------------------------------------------------------------- inference

    def predict(
        self,
        model: str,
        ref: str = "production",
        *,
        images: Sequence[str | bytes | BinaryIO] | None = None,
        image_urls: Sequence[str] | None = None,
        overrides: dict[str, Any] | None = None,
        include_overlay: bool = False,
        trace_id: str | None = None,
        timeout: float | None = None,
    ) -> Prediction:
        """Run inference. `ref` is a version number or a tag such as 'production'."""
        url = f"{self.base_url}/v1/models/{model}/{ref}/predict"
        headers = self._headers({"traceparent": trace_id} if trace_id else None)

        if image_urls and not images:
            payload: dict[str, Any] = {"image_urls": list(image_urls)}
            if overrides:
                payload["overrides"] = overrides
            payload["include_overlay"] = include_overlay
            response = self._client.post(url, json=payload, headers=headers, timeout=timeout)
        else:
            files = [("images", _to_file(item)) for item in (images or [])]
            data: dict[str, str] = {"include_overlay": str(include_overlay).lower()}
            if overrides:
                import json

                data["overrides"] = json.dumps(overrides)
            response = self._client.post(url, files=files, data=data, headers=headers,
                                         timeout=timeout)

        if response.status_code >= 400:
            self._raise(response)
        return Prediction(response.json())

    def predict_base64(self, model: str, ref: str, images: Iterable[bytes],
                       **kwargs: Any) -> Prediction:
        """For callers that already hold bytes and would rather not touch multipart."""
        url = f"{self.base_url}/v1/models/{model}/{ref}/predict"
        payload = {"images": [base64.b64encode(blob).decode() for blob in images], **kwargs}
        response = self._client.post(url, json=payload, headers=self._headers())
        if response.status_code >= 400:
            self._raise(response)
        return Prediction(response.json())

    # --------------------------------------------------------------- async jobs

    def submit_job(self, *, kind: str, model: str, ref: str = "production",
                   source_keys: Sequence[str] | None = None,
                   source_urls: Sequence[str] | None = None, video_key: str | None = None,
                   frame_stride: int = 1, webhook_url: str | None = None) -> dict:
        response = self._client.post(
            f"{self.base_url}/v1/jobs",
            json={"kind": kind, "model": model, "ref": ref,
                  "source_keys": list(source_keys or []) or None,
                  "source_urls": list(source_urls or []) or None,
                  "video_key": video_key, "frame_stride": frame_stride,
                  "webhook_url": webhook_url},
            headers=self._headers(),
        )
        if response.status_code >= 400:
            self._raise(response)
        return response.json()

    def job(self, job_id: str) -> dict:
        response = self._client.get(f"{self.base_url}/v1/jobs/{job_id}", headers=self._headers())
        if response.status_code >= 400:
            self._raise(response)
        return response.json()

    def wait_for_job(self, job_id: str, *, poll_seconds: float = 3.0,
                     timeout_seconds: float = 3600) -> dict:
        import time

        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            job = self.job(job_id)
            if job["status"] in ("succeeded", "failed", "cancelled"):
                return job
            time.sleep(poll_seconds)
        raise TimeoutError(f"job {job_id} did not finish within {timeout_seconds}s")

    # ------------------------------------------------------------------ registry

    def models(self) -> list[dict]:
        response = self._client.get(f"{self.base_url}/v1/models", headers=self._headers())
        if response.status_code >= 400:
            self._raise(response)
        return response.json()

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "Aperture":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _to_file(item: str | bytes | BinaryIO):
    if isinstance(item, str):
        return (os.path.basename(item), open(item, "rb"), "application/octet-stream")
    if isinstance(item, bytes):
        return ("image", item, "application/octet-stream")
    return (getattr(item, "name", "image"), item, "application/octet-stream")
