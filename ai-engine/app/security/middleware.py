"""HTTP middleware: request ids, security headers, authentication and rate limiting."""

from __future__ import annotations

import logging
import time
import uuid

from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

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


class BodyTooLargeError(StarletteHTTPException):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(status_code=413, detail=message)
        self.code = code


class BodySizeLimitMiddleware:
    """Pure ASGI middleware that caps request bodies while they stream in.

    Works with and without ``Content-Length`` (chunked uploads), so oversized bodies are
    never fully read, parsed or spooled to disk.
    """

    def __init__(self, app: ASGIApp, *, file_limit: int, default_limit: int = 1024 * 1024) -> None:
        self.app = app
        self.file_limit = file_limit
        self.default_limit = default_limit

    def _limit(self, path: str) -> tuple[int, str, str]:
        if path == "/files/upload":
            mb = self.file_limit / (1024 * 1024)
            # + multipart overhead
            return self.file_limit + 64 * 1024, "file_too_large", f"File exceeds the {mb:g} MB limit."
        return self.default_limit, "payload_too_large", "Request body is too large."

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit, code, message = self._limit(scope.get("path", ""))
        for name, value in scope.get("headers", []):
            if name == b"content-length" and value.isdigit() and int(value) > limit:
                await self._reject(send, code, message)
                return

        received = 0
        exceeded = False
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received, exceeded
            msg = await receive()
            if msg["type"] == "http.request":
                received += len(msg.get("body", b""))
                if received > limit:
                    exceeded = True
                    raise BodyTooLargeError(code, message)
            return msg

        async def guarded_send(msg: Message) -> None:
            nonlocal response_started
            if exceeded:
                # The framework may turn the aborted read into a generic 400; answer 413 instead.
                if msg["type"] == "http.response.start" and not response_started:
                    response_started = True
                    await self._reject(send, code, message)
                return
            if msg["type"] == "http.response.start":
                response_started = True
            await send(msg)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except BodyTooLargeError:
            if not response_started:
                response_started = True
                await self._reject(send, code, message)

    @staticmethod
    async def _reject(send: Send, code: str, message: str) -> None:
        response = JSONResponse(status_code=413, content=error_body(code, message))
        await send({"type": "http.response.start", "status": 413, "headers": response.raw_headers})
        await send({"type": "http.response.body", "body": response.body})
