"""
Rate Limiting Middleware — Token Bucket algorithm for the Scheduler API.

Supports:
  - Global rate limit (shared across all requests)
  - Per-backend rate limit (per backend_id, enforced before routing)

Token Bucket: capacity tokens max, refill at refill_rate per second.
Allows burst traffic up to capacity, then throttles.
"""

import asyncio
import logging
import time
from typing import Dict

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger("rate_limit")


class TokenBucket:
    """Single token bucket for rate limiting."""

    def __init__(self, capacity: int, refill_rate: float):
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.tokens = float(capacity)
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def consume(self, tokens: int = 1) -> bool:
        """Try to consume tokens. Returns True if allowed, False if rate limited."""
        async with self._lock:
            self._refill()
            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            return False

    def _refill(self):
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now

    @property
    def available(self) -> float:
        return self.tokens


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    FastAPI middleware that enforces global + per-backend rate limits.

    Per-backend limits are enforced after routing (in the gateway handler),
    but the global limit is applied here at the middleware level.
    """

    PUBLIC_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}

    def __init__(self, app, global_bucket: TokenBucket, enabled: bool = True):
        super().__init__(app)
        self.global_bucket = global_bucket
        self.enabled = enabled

    async def dispatch(self, request: Request, call_next):
        # Skip rate limiting for public paths
        if request.url.path in self.PUBLIC_PATHS or request.url.path.startswith("/docs"):
            return await call_next(request)

        # Skip if disabled
        if not self.enabled:
            return await call_next(request)

        # Global rate limit check
        if not await self.global_bucket.consume(1):
            logger.warning(f"Global rate limit exceeded for {request.url.path}")
            return JSONResponse(
                status_code=429,
                content={
                    "error": "Too Many Requests",
                    "detail": "Global rate limit exceeded. Please retry later.",
                    "retry_after": int(1 / self.global_bucket.refill_rate),
                },
            )

        response = await call_next(request)

        # Add rate limit headers
        response.headers["X-RateLimit-Remaining"] = str(int(self.global_bucket.available))
        response.headers["X-RateLimit-Limit"] = str(self.global_bucket.capacity)

        return response


class PerBackendRateLimiter:
    """Rate limits keyed by backend_id, enforced at the gateway handler level."""

    def __init__(self, default_capacity: int = 10, default_refill_rate: float = 5.0):
        self.default_capacity = default_capacity
        self.default_refill_rate = default_refill_rate
        self._buckets: Dict[str, TokenBucket] = {}

    def get_bucket(self, backend_id: str) -> TokenBucket:
        """Get or create a token bucket for a specific backend."""
        if backend_id not in self._buckets:
            self._buckets[backend_id] = TokenBucket(
                capacity=self.default_capacity,
                refill_rate=self.default_refill_rate,
            )
            logger.info(f"Created rate limit bucket for backend '{backend_id}'")
        return self._buckets[backend_id]

    async def consume(self, backend_id: str, tokens: int = 1) -> bool:
        """Try to consume tokens for a backend. Returns True if allowed."""
        bucket = self.get_bucket(backend_id)
        return await bucket.consume(tokens)


def create_rate_limit_middleware(config: dict):
    """Factory function: create RateLimitMiddleware + PerBackendRateLimiter from config."""
    rl_cfg = config.get("rate_limit", {})
    global_cfg = rl_cfg.get("global", {})
    per_backend_cfg = rl_cfg.get("per_backend", {})

    enabled = global_cfg.get("enabled", True)
    capacity = global_cfg.get("capacity", 20)
    refill_rate = global_cfg.get("refill_rate", 10)

    global_bucket = TokenBucket(capacity=capacity, refill_rate=refill_rate)

    backend_limiter = PerBackendRateLimiter(
        default_capacity=per_backend_cfg.get("default_capacity", 10),
        default_refill_rate=per_backend_cfg.get("default_refill_rate", 5),
    )

    middleware = lambda app: RateLimitMiddleware(
        app, global_bucket=global_bucket, enabled=enabled
    )

    return middleware, backend_limiter