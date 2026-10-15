"""HTTP middlewares for request correlation, body size enforcement, and access logging."""
import json
import logging
import re
import time
import uuid
from typing import Callable, Optional
from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from src.core.config import get_settings
from src.core.logging import request_id_ctx

logger = logging.getLogger(__name__)

# Valid request ID format: alphanumeric with hyphens/underscores, max 64 chars
REQUEST_ID_REGEX = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")


class RequestIdMiddleware(BaseHTTPMiddleware):
    """
    Middleware to ensure every request has a validated correlation ID.
    
    If client provides an X-Request-ID that conforms to safe format, it is reused.
    Otherwise, a cryptographically random UUIDv4 is generated.
    The ID is set in request_id_ctx and returned in the X-Request-ID response header.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        client_req_id = request.headers.get("x-request-id")
        if client_req_id and REQUEST_ID_REGEX.match(client_req_id):
            request_id = client_req_id
        else:
            request_id = str(uuid.uuid4())

        token = request_id_ctx.set(request_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            request_id_ctx.reset(token)


class RequestBodyLimitMiddleware:
    """
    Pure ASGI middleware enforcing maximum request body size (default 64 KB).
    Protects the API against memory exhaustion and oversized payloads.

    Handles both:
    1. Declared Content-Length headers (fast-path rejection for oversized or invalid/negative values).
    2. Chunked transfer encoding / streamed payloads without Content-Length by counting received bytes on the fly.
    """

    def __init__(self, app, max_bytes: Optional[int] = None):
        self.app = app
        self._max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        settings = get_settings()
        max_bytes = self._max_bytes or settings.MAX_REQUEST_BODY_BYTES

        # 1. Fast path: check declared Content-Length header
        content_length = None
        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length":
                try:
                    content_length = int(value.decode("latin1"))
                    if content_length < 0:
                        resp = JSONResponse(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            content={"detail": "Invalid Content-Length header."},
                        )
                        await resp(scope, receive, send)
                        return
                    if content_length > max_bytes:
                        resp = JSONResponse(
                            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                            content={
                                "detail": (
                                    f"Request body exceeds maximum allowed size of "
                                    f"{max_bytes} bytes."
                                )
                            },
                        )
                        await resp(scope, receive, send)
                        return
                except ValueError:
                    resp = JSONResponse(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        content={"detail": "Invalid Content-Length header."},
                    )
                    await resp(scope, receive, send)
                    return
                break

        # 2. Streaming check: count incoming bytes on the fly
        received_bytes = 0
        limit_exceeded = False

        async def limited_receive():
            nonlocal received_bytes, limit_exceeded
            message = await receive()
            if message["type"] == "http.request":
                chunk = message.get("body", b"")
                received_bytes += len(chunk)
                if received_bytes > max_bytes:
                    limit_exceeded = True
                    raise ValueError(f"Request body exceeds maximum allowed size of {max_bytes} bytes.")
            return message

        async def intercepted_send(message):
            if limit_exceeded:
                if message["type"] == "http.response.start":
                    body_bytes = json.dumps(
                        {"detail": f"Request body exceeds maximum allowed size of {max_bytes} bytes."}
                    ).encode("utf-8")
                    headers = [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body_bytes)).encode("latin1")),
                    ]
                    await send({
                        "type": "http.response.start",
                        "status": status.HTTP_413_CONTENT_TOO_LARGE,
                        "headers": headers,
                    })
                    await send({
                        "type": "http.response.body",
                        "body": body_bytes,
                        "more_body": False,
                    })
                return
            await send(message)

        try:
            await self.app(scope, limited_receive, intercepted_send)
        except Exception:
            if limit_exceeded:
                resp = JSONResponse(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    content={"detail": f"Request body exceeds maximum allowed size of {max_bytes} bytes."},
                )
                await resp(scope, receive, send)
            else:
                raise


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware logging incoming HTTP requests and response performance metrics.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start_time = time.perf_counter()
        req_id = request_id_ctx.get()

        client_host = request.client.host if request.client else "unknown"
        method = request.method
        path = request.url.path

        try:
            response = await call_next(request)
            duration_ms = round((time.perf_counter() - start_time) * 1000.0, 2)

            # Avoid spamming health check logs at INFO level unless it failed
            if path in ("/health", "/health/ready") and response.status_code == 200:
                logger.debug(
                    "HTTP %s %s %s - %.2fms",
                    method,
                    path,
                    response.status_code,
                    duration_ms,
                    extra={
                        "endpoint": path,
                        "method": method,
                        "status_code": response.status_code,
                        "duration_ms": duration_ms,
                        "client_ip": client_host,
                    },
                )
            else:
                logger.info(
                    "HTTP %s %s %s - %.2fms",
                    method,
                    path,
                    response.status_code,
                    duration_ms,
                    extra={
                        "endpoint": path,
                        "method": method,
                        "status_code": response.status_code,
                        "duration_ms": duration_ms,
                        "client_ip": client_host,
                    },
                )
            return response
        except Exception as exc:
            duration_ms = round((time.perf_counter() - start_time) * 1000.0, 2)
            logger.error(
                "Unhandled exception in HTTP %s %s: %s",
                method,
                path,
                type(exc).__name__,
                extra={
                    "endpoint": path,
                    "method": method,
                    "status_code": 500,
                    "duration_ms": duration_ms,
                    "client_ip": client_host,
                    "error_type": type(exc).__name__,
                },
            )
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "detail": "An unexpected internal server error occurred while processing your query."
                },
                headers={"X-Request-ID": req_id or ""},
            )
