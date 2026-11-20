"""HTTP middleware: request ids, security headers, authentication and rate limiting."""

from __future__ import annotations

import logging
import time
import uuid

from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.errors import AuthError, error_body
from app.security.auth import authenticate, extract_credential

logger = logging.getLogger("app.http")

PUBLIC_PATHS = frozenset({"/", "/health", "/docs", "/docs/oauth2-redirect", "/redoc",
                          "/openapi.json", "/favicon.ico"})
PUBLIC_PREFIXES = ("/demo",)


def is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or any(path == p or path.startswith(p + "/") for p in PUBLIC_PREFIXES)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request id, adds security headers and writes an access log line."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        started = time.perf_counter()
        too_large = self._body_too_large(request)
        if too_large:
            return JSONResponse(status_code=413, content=error_body(
                too_large[0], too_large[1], request_id=request_id))
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/demo"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
            )
        if not is_public(request.url.path):
            response.headers["Cache-Control"] = "no-store"
        logger.info("http_request", extra={
            "request_id": request_id, "method": request.method, "path": request.url.path,
            "status": response.status_code, "duration_ms": int((time.perf_counter() - started) * 1000),
        })
        return response


    @staticmethod
    def _body_too_large(request: Request) -> tuple[str, str] | None:
        """Reject oversized bodies early, before multipart parsing spools them to disk."""
        length = request.headers.get("content-length")
        if not length or not length.isdigit():
            return None
        settings = request.app.state.settings
        if request.url.path == "/files/upload":
            limit = settings.max_file_size_bytes + 64 * 1024  # multipart overhead
            error = ("file_too_large", f"File exceeds the {settings.max_file_size_mb:g} MB limit.")
        else:
            limit = 1024 * 1024
            error = ("payload_too_large", "Request body is too large.")
        return error if int(length) > limit else None


class AuthMiddleware(BaseHTTPMiddleware):
    """Resolves the caller from verified credentials and enforces per-user rate limits."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method == "OPTIONS" or is_public(request.url.path):
            return await call_next(request)

        app = request.app
        settings = app.state.settings
        client_ip = request.client.host if request.client else "unknown"
        credential = extract_credential(request.headers.get("authorization"), request.headers.get("x-api-key"))
        if credential is None:
            return self._deny(request, AuthError("Authentication required", code="auth_required"))

        def _auth():
            with app.state.session_factory() as db:
                return authenticate(db, settings, credential)

        try:
            principal = await run_in_threadpool(_auth)
        except AuthError as exc:
            allowed, retry_after = app.state.auth_failure_limiter.hit(f"ip:{client_ip}")
            if not allowed:
                return JSONResponse(status_code=429, headers={"Retry-After": str(retry_after)},
                                    content=error_body("rate_limited", "Too many failed authentication attempts",
                                                       request_id=request.state.request_id))
            return self._deny(request, exc)

        allowed, retry_after = app.state.rate_limiter.hit(principal.rate_limit_key)
        if not allowed:
            return JSONResponse(status_code=429, headers={"Retry-After": str(retry_after)},
                                content=error_body("rate_limited", "Rate limit exceeded, retry later",
                                                   request_id=request.state.request_id))
        request.state.principal = principal
        return await call_next(request)

    @staticmethod
    def _deny(request: Request, exc: AuthError) -> Response:
        return JSONResponse(status_code=401, headers={"WWW-Authenticate": "Bearer"},
                            content=error_body(exc.code, exc.message,
                                               request_id=getattr(request.state, "request_id", None)))
