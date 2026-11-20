"""Application errors and safe JSON error responses.

Clients always receive ``{"error": {"code", "message", ...}}``. Stack traces, SQL,
file paths and user input are never echoed back.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # FastAPI is imported lazily: the document worker process uses these
    from fastapi import FastAPI, Request  # error classes and must start quickly (notably on Windows).

logger = logging.getLogger("app.errors")


class AppError(Exception):
    """An expected error with a stable machine-readable code."""

    status_code: int = 400
    code: str = "bad_request"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        self.details = details or {}
        self.headers = headers or {}


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class AuthError(AppError):
    status_code = 401
    code = "unauthorized"


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"


class LimitExceededError(AppError):
    status_code = 429
    code = "limit_exceeded"


class FileValidationError(AppError):
    status_code = 422
    code = "invalid_file"


class LLMError(AppError):
    """Raised by the LLM layer. ``retryable`` marks transient upstream failures."""

    status_code = 502
    code = "llm_error"

    def __init__(self, message: str, *, code: str = "llm_error", status_code: int = 502,
                 retryable: bool = False, retry_after: float | None = None) -> None:
        headers = {"Retry-After": str(max(1, int(retry_after + 0.999)))} if retry_after is not None else None
        super().__init__(message, code=code, status_code=status_code, headers=headers)
        self.retryable = retryable
        self.retry_after = retry_after


def error_body(code: str, message: str, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"code": code, "message": message}
    body.update({k: v for k, v in extra.items() if v is not None})
    return {"error": body}


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def install_error_handlers(app: FastAPI) -> None:
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse
    from starlette.exceptions import HTTPException as StarletteHTTPException

    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, exc.message, request_id=_request_id(request),
                               **({"details": exc.details} if exc.details else {})),
            headers=exc.headers or None,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Drop "input"/"ctx" so user prompts and file contents are never reflected back.
        issues = [
            {"loc": [str(p) for p in err.get("loc", ())], "msg": err.get("msg", ""), "type": err.get("type", "")}
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=error_body("validation_error", "Request validation failed",
                               request_id=_request_id(request), details={"issues": issues}),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = getattr(exc, "code", None) or {404: "not_found", 405: "method_not_allowed", 401: "unauthorized",
                                              403: "forbidden", 413: "payload_too_large"}.get(exc.status_code,
                                                                                             "http_error")
        message = exc.detail if isinstance(exc.detail, str) else "HTTP error"
        return JSONResponse(status_code=exc.status_code,
                            content=error_body(code, message, request_id=_request_id(request)))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled_error", extra={"error_type": type(exc).__name__,
                                               "request_id": _request_id(request)})
        return JSONResponse(status_code=500,
                            content=error_body("internal_error", "Internal server error",
                                               request_id=_request_id(request)))
