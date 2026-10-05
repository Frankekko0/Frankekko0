"""HTTP middleware: request id + access log, and security headers."""

from __future__ import annotations

import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger("app.http")
QUIET_PATHS = ("/health", "/api/v1/health", "/api/v1/demo/images")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get("x-request-id", "")
        request_id = (
            incoming if 8 <= len(incoming) <= 64 and incoming.replace("-", "").isalnum() else uuid.uuid4().hex
        )
        request.state.request_id = request_id
        structlog.contextvars.bind_contextvars(request_id=request_id)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
        duration_ms = round((time.perf_counter() - start) * 1000, 1)
        response.headers["X-Request-ID"] = request_id
        if not request.url.path.startswith(QUIET_PATHS):
            log.info(
                "http.request",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                duration_ms=duration_ms,
                request_id=request_id,
            )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        h = response.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        path = request.url.path
        if not path.startswith(("/docs", "/redoc")):
            if path.startswith("/api/v1/demo/images"):
                h.setdefault("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
            else:
                h.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        if get_settings().cookie_secure:
            h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if path.startswith("/api/") and "Cache-Control" not in h:
            h["Cache-Control"] = "no-store"
        return response
