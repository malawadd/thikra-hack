"""Cross-cutting HTTP request metadata and safe request logging."""

from __future__ import annotations

import logging
import time

from fastapi import Request
from fastapi.responses import JSONResponse

from app.commerce.rate_limit import rate_limiter
from app.logging_setup import new_request_id, request_id_var


async def request_logging(request: Request, call_next):
    """Attach a request id and record non-secret method/path/status metadata."""
    logger = logging.getLogger("api.main")
    rid = new_request_id()
    token = request_id_var.set(rid)
    start = time.perf_counter()
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > 1_048_576:
        request_id_var.reset(token)
        return JSONResponse(
            status_code=413,
            content={
                "detail": {
                    "code": "REQUEST_TOO_LARGE",
                    "message": "Request bodies are limited to 1 MiB.",
                }
            },
            headers={"X-Request-Id": rid},
        )
    rate_limit = rate_limiter.check(request)
    if rate_limit is not None and not rate_limit.allowed:
        request_id_var.reset(token)
        return JSONResponse(
            status_code=429,
            content={
                "detail": {
                    "code": "RATE_LIMITED",
                    "message": "The Agent Gateway request limit was exceeded.",
                }
            },
            headers={
                "X-Request-Id": rid,
                "Retry-After": str(rate_limit.retry_after),
                "X-RateLimit-Limit": str(rate_limit.limit),
                "X-RateLimit-Remaining": "0",
            },
        )
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "request crashed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "duration_ms": int((time.perf_counter() - start) * 1000),
            },
        )
        request_id_var.reset(token)
        raise
    response.headers["X-Request-Id"] = rid
    if rate_limit is not None:
        response.headers["X-RateLimit-Limit"] = str(rate_limit.limit)
        response.headers["X-RateLimit-Remaining"] = str(rate_limit.remaining)
    logger.info(
        "request",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": int((time.perf_counter() - start) * 1000),
        },
    )
    request_id_var.reset(token)
    return response
