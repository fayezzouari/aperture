"""Structured JSON logs with the request/trace ids stitched in (R5.1, R5.3)."""

from __future__ import annotations

import json
import logging
import re
import sys
from contextvars import ContextVar

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)

# Roboflow and others take the key in the query string, and httpx logs every URL
# it requests - so a credential in a URL is scrubbed from every line written.
_SECRET_PARAM = re.compile(r"(?<![A-Za-z0-9_])((?:api_key|apikey|access_token|token|key)=)[^&\s\"']+",
                           re.IGNORECASE)


def redact(text: str) -> str:
    return _SECRET_PARAM.sub(r"\1***", text)


_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "asctime", "message", "taskName"
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S.%03dZ"),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        if rid := request_id_var.get():
            payload["request_id"] = rid
        if tid := trace_id_var.get():
            payload["trace_id"] = tid
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    for noisy in ("uvicorn.access", "botocore", "boto3", "s3transfer", "urllib3", "asyncio"):
        logging.getLogger(noisy).setLevel("WARNING")
    logging.getLogger("uvicorn.error").handlers = [handler]
