"""ASGI middleware: per-request structured access logging.

Every API call produces exactly one log line with method/path/status/duration.
Unhandled exceptions are caught, logged with a full traceback and re-raised so
FastAPI's normal exception handlers (or the uvicorn 500 fallback) still apply.

The middleware also assigns/propagates a ``request_id`` (X-Request-ID header)
so a request can be traced through every downstream log message via
``easyobs.logging_setup.request_id_var``.
"""
from __future__ import annotations

import logging
import time
import uuid

from starlette.requests import Request
from starlette.types import ASGIApp

from easyobs.logging_setup import request_id_var

REQUEST_ID_HEADER = "x-request-id"

_log = logging.getLogger("easyobs.access")


class RequestLoggingMiddleware:
    """Lightweight ASGI middleware (no BaseHTTPMiddleware overhead)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):  # type: ignore[no-untyped-def]
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        incoming_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex[:12]
        token = request_id_var.set(incoming_id)

        status_holder = {"code": 500}

        async def send_wrapper(message):  # type: ignore[no-untyped-def]
            if message["type"] == "http.response.start":
                status_holder["code"] = message["status"]
                headers = list(message.get("headers", []))
                headers.append(
                    (REQUEST_ID_HEADER.encode("latin-1"), incoming_id.encode("latin-1"))
                )
                message = {**message, "headers": headers}
            await send(message)

        started = time.perf_counter()
        client_ip = request.client.host if request.client else "-"
        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            duration_ms = (time.perf_counter() - started) * 1000
            _log.exception(
                "%s %s -> 500 (%.1fms) [unhandled]",
                request.method,
                request.url.path,
                duration_ms,
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": 500,
                    "duration_ms": round(duration_ms, 1),
                    "client_ip": client_ip,
                },
            )
            raise
        else:
            duration_ms = (time.perf_counter() - started) * 1000
            status = status_holder["code"]
            level = (
                logging.WARNING
                if 400 <= status < 500
                else logging.ERROR
                if status >= 500
                else logging.INFO
            )
            _log.log(
                level,
                "%s %s -> %d (%.1fms)",
                request.method,
                request.url.path,
                status,
                duration_ms,
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": status,
                    "duration_ms": round(duration_ms, 1),
                    "client_ip": client_ip,
                    "query": str(request.url.query) or None,
                },
            )
        finally:
            request_id_var.reset(token)


__all__ = ["RequestLoggingMiddleware", "REQUEST_ID_HEADER"]
