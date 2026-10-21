"""HTTP hardening middleware: security headers and a request-body size limit."""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .config import get_settings

API_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
    # The API only serves JSON (plus Swagger UI in dev); nothing may frame it.
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}
DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        for k, v in API_HEADERS.items():
            if k == "Content-Security-Policy" and request.url.path.startswith(DOCS_PATHS):
                continue  # Swagger UI loads its own assets; it is disabled in production
            response.headers.setdefault(k, v)
        if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
            response.headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains")
        return response


class BodySizeLimit(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        limit = get_settings().max_body_bytes
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            return JSONResponse({"detail": "request body too large"}, status_code=413)
        if request.method in ("POST", "PUT", "PATCH") and not declared:
            body = await request.body()  # chunked: enforce on the actual size
            if len(body) > limit:
                return JSONResponse({"detail": "request body too large"}, status_code=413)
        return await call_next(request)
