"""Small process-local sliding-window guard for public agent traffic."""

from __future__ import annotations

import hashlib
import math
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi import Request

from app.config import settings


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int = 0


class SlidingWindowRateLimiter:
    """Bound API-key/client request counts without retaining bearer tokens."""

    def __init__(self) -> None:
        self._requests: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    @staticmethod
    def _identity(request: Request) -> str:
        authorization = request.headers.get("authorization")
        if authorization:
            return "key:" + hashlib.sha256(authorization.encode()).hexdigest()[:24]
        return "client:" + (request.client.host if request.client else "unknown")

    def _consume(
        self,
        identity: str,
        bucket: str,
        *,
        limit: int,
        window_seconds: int,
        now: float,
    ) -> RateLimitDecision:
        timestamps = self._requests[(identity, bucket)]
        cutoff = now - window_seconds
        while timestamps and timestamps[0] <= cutoff:
            timestamps.popleft()
        if len(timestamps) >= limit:
            retry_after = max(1, math.ceil(timestamps[0] + window_seconds - now))
            return RateLimitDecision(False, limit, 0, retry_after)
        timestamps.append(now)
        return RateLimitDecision(True, limit, limit - len(timestamps))

    def check(self, request: Request, *, now: float | None = None) -> RateLimitDecision | None:
        path = request.url.path
        if not (path.startswith("/api/v1/") or path.startswith("/mcp")):
            return None
        current = time.monotonic() if now is None else now
        identity = self._identity(request)
        window = settings.thikra_rate_limit_window_seconds
        with self._lock:
            overall = self._consume(
                identity,
                "gateway",
                limit=settings.thikra_rate_limit_requests,
                window_seconds=window,
                now=current,
            )
            if not overall.allowed:
                return overall
            if request.method == "POST" and path == "/api/v1/quotes":
                return self._consume(
                    identity,
                    "quotes",
                    limit=settings.thikra_quote_rate_limit_requests,
                    window_seconds=window,
                    now=current,
                )
            return overall

    def clear(self) -> None:
        with self._lock:
            self._requests.clear()


rate_limiter = SlidingWindowRateLimiter()
