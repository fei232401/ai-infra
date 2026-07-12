"""
Auth Middleware — Unified authentication for the Scheduler API.

Supports:
  - Bearer Token (static API Key list from config)
  - JWT HS256 (future extension point, structure ready)

Applied at FastAPI middleware level — intercepts all requests before routing.
"""

import logging
from typing import List, Optional

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger("auth_middleware")


class AuthMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware that validates Bearer tokens on every request."""

    # Public paths that skip authentication
    PUBLIC_PATHS = {
        "/health",
        "/docs",
        "/openapi.json",
        "/redoc",
    }

    def __init__(self, app, api_keys: Optional[List[str]] = None, enabled: bool = True):
        super().__init__(app)
        self.api_keys = set(api_keys or [])
        self.enabled = enabled

    async def dispatch(self, request: Request, call_next):
        # Skip auth for public paths
        if request.url.path in self.PUBLIC_PATHS or request.url.path.startswith("/docs"):
            return await call_next(request)

        # Skip if auth is disabled
        if not self.enabled:
            return await call_next(request)

        # Extract Bearer token
        auth_header = request.headers.get("Authorization", "")
        api_key = auth_header.replace("Bearer ", "").strip()

        if not api_key:
            logger.warning(f"Auth rejected: no token for {request.url.path}")
            return JSONResponse(
                status_code=401,
                content={
                    "error": "Unauthorized",
                    "detail": "Missing Authorization header. Use 'Authorization: Bearer <your-api-key>'",
                },
            )

        # Validate against known API keys
        if api_key not in self.api_keys:
            logger.warning(f"Auth rejected: invalid token for {request.url.path}")
            return JSONResponse(
                status_code=401,
                content={
                    "error": "Unauthorized",
                    "detail": "Invalid API key.",
                },
            )

        # Inject authenticated context into request state
        request.state.api_key = api_key
        request.state.is_authenticated = True

        return await call_next(request)


def create_auth_middleware(config: dict):
    """Factory function: create AuthMiddleware from config."""
    auth_cfg = config.get("auth", {})
    enabled = auth_cfg.get("enabled", True)
    api_keys = auth_cfg.get("api_keys", [])
    return lambda app: AuthMiddleware(app, api_keys=api_keys, enabled=enabled)