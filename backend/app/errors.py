"""R3.7 - a single error contract across the whole API.

Every failure leaves the process as {"error": {"code", "message", "request_id",
"details"}} with an HTTP status, so a client can branch on `code` and never has
to parse prose.
"""

from __future__ import annotations

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

# code -> default HTTP status. Codes are permanent API surface: add, never rename.
ERROR_STATUS: dict[str, int] = {
    "unauthenticated": 401,
    "invalid_credentials": 401,
    "api_key_invalid": 401,
    "api_key_revoked": 401,
    "api_key_expired": 401,
    "token_invalid": 401,
    "token_revoked": 401,
    "token_expired": 401,
    "forbidden": 403,
    "key_not_scoped_to_model": 403,
    "insufficient_role": 403,
    "not_found": 404,
    "model_not_found": 404,
    "version_not_found": 404,
    "tag_not_found": 404,
    "job_not_found": 404,
    "no_workspace": 404,
    "conflict": 409,
    "version_immutable": 409,
    "slug_taken": 409,
    "tag_locked": 409,
    "upload_incomplete": 409,
    "invalid_request": 400,
    "invalid_image": 400,
    "invalid_config": 400,
    "unsupported_format": 400,
    "batch_too_large": 400,
    "payload_too_large": 413,
    "validation_failed": 422,
    "rate_limited": 429,
    "quota_exceeded": 429,
    "queue_full": 503,
    "no_replica_available": 503,
    "model_not_deployed": 503,
    "runtime_unhealthy": 503,
    "upstream_error": 502,
    "inference_failed": 500,
    "timeout": 504,
    "internal_error": 500,
}


class ApiError(Exception):
    def __init__(self, code: str, message: str, *, details: dict | None = None,
                 http_status: int | None = None):
        self.code = code
        self.message = message
        self.details = details
        self.http_status = http_status or ERROR_STATUS.get(code, 400)
        super().__init__(message)


def error_payload(code: str, message: str, request_id: str | None = None,
                  details: dict | None = None) -> dict:
    body: dict = {"code": code, "message": message}
    if request_id:
        body["request_id"] = request_id
    if details:
        body["details"] = details
    return {"error": body}


def _constraint_name(exc: IntegrityError) -> str | None:
    """The violated constraint, when the driver reports one (asyncpg does, SQLite does not)."""
    orig = getattr(exc, "orig", None)
    for candidate in (orig, getattr(orig, "__cause__", None)):
        name = getattr(candidate, "constraint_name", None)
        if name:
            return str(name)
    return None


def install_handlers(app) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        rid = getattr(request.state, "request_id", None)
        return JSONResponse(
            status_code=exc.http_status,
            content=error_payload(exc.code, exc.message, rid, exc.details),
            headers={"x-request-id": rid or ""},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        rid = getattr(request.state, "request_id", None)
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=error_payload(
                "validation_failed", "the request body or query is not valid", rid,
                {"errors": [
                    {"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]}
                    for e in exc.errors()[:20]
                ]},
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        rid = getattr(request.state, "request_id", None)
        code = {401: "unauthenticated", 403: "forbidden", 404: "not_found",
                405: "invalid_request", 413: "payload_too_large"}.get(exc.status_code, "internal_error")
        detail = exc.detail if isinstance(exc.detail, str) else "request failed"
        return JSONResponse(status_code=exc.status_code,
                            content=error_payload(code, detail, rid))

    @app.exception_handler(IntegrityError)
    async def _integrity(request: Request, exc: IntegrityError) -> JSONResponse:
        # A check-then-insert that lost a race to a concurrent request: the
        # database kept the data consistent, so this is the caller's conflict to
        # retry, not a server fault.
        rid = getattr(request.state, "request_id", None)
        import logging

        constraint = _constraint_name(exc)
        logging.getLogger("aperture.error").warning(
            "integrity conflict on %s", request.url.path, extra={"constraint": constraint})
        return JSONResponse(
            status_code=409,
            content=error_payload(
                "conflict",
                "another request changed the same record at the same time; retry the request",
                rid, {"constraint": constraint} if constraint else None,
            ),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        rid = getattr(request.state, "request_id", None)
        import logging

        logging.getLogger("aperture.error").exception("unhandled error on %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content=error_payload("internal_error", "an unexpected error occurred", rid),
        )
