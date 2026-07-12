"""
Scheduler Core — main entry point that ties together:
  - BackendRegistry (health checks + scoring)
  - RoutingEngine (strategy-based backend selection)
  - LoadBalancer (adaptive distribution)

This module is imported by the unified API gateway.
"""

import logging
import time
from typing import Dict, Optional

import aiohttp

from backend_registry import Backend, BackendRegistry, load_config
from load_balancer import LoadBalancer
from router import RoutingEngine

logger = logging.getLogger("scheduler")


class Scheduler:
    """
    Central scheduler that orchestrates all routing decisions.

    Usage:
        config = load_config()
        scheduler = Scheduler(config)
        await scheduler.start()

        backend, routed_by = scheduler.route_request(
            model="qwen2.5:0.5b",
            strategy="latency",
            session_id="user-123"
        )

        # ... use backend to forward request ...
        scheduler.record_result(backend.id, success=True, ttft_ms=45, tps=200, tokens=50)

        await scheduler.stop()
    """

    def __init__(self, config: dict):
        self.config = config
        self.registry = BackendRegistry(config)
        self.router = RoutingEngine(self.registry, config)
        self.load_balancer = LoadBalancer(strategy="adaptive")

        # Session affinity map: session_id → backend_id
        self.session_map: Dict[str, str] = {}
        self.session_ttl = config.get("scheduler", {}).get("session_ttl", 1800)
        self._session_timestamps: Dict[str, float] = {}

        # Stats
        self.total_requests = 0
        self.successful_requests = 0
        self.failed_requests = 0
        self.start_time = 0.0

        # Shared HTTP session pool (managed lifecycle)
        self._http_session: Optional[aiohttp.ClientSession] = None

    # --- Lifecycle ---

    async def start(self):
        """Initialize registry, session pool, and background health checks."""
        self.registry.load_from_config()
        self._http_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=120),
            connector=aiohttp.TCPConnector(limit=100, ttl_dns_cache=300),
        )
        await self.registry.start()
        self.start_time = time.time()
        logger.info("Scheduler started")

    async def stop(self):
        """Graceful shutdown."""
        await self.registry.stop()
        if self._http_session:
            await self._http_session.close()
        logger.info("Scheduler stopped")

    async def get_http_session(self) -> aiohttp.ClientSession:
        """Return the shared HTTP session for backend proxy requests."""
        if self._http_session is None or self._http_session.closed:
            self._http_session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=120),
                connector=aiohttp.TCPConnector(limit=100, ttl_dns_cache=300),
            )
        return self._http_session

    # --- Request Routing ---

    def route_request(
        self,
        model: str,
        strategy: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> tuple:
        """
        Route an inference request to the best available backend.

        Args:
            model: Requested model name.
            strategy: Routing strategy override (latency|throughput|cost).
            session_id: Optional session for affinity routing.

        Returns:
            (Backend, routing_info_dict) or (None, error_dict)
        """
        self.total_requests += 1

        # Clean expired sessions
        self._clean_sessions()

        # Select backend via routing engine
        backend = self.router.select_backend(
            model=model,
            strategy=strategy,
            session_id=session_id,
            session_map=self.session_map,
        )

        if backend is None:
            self.failed_requests += 1
            return None, {
                "error": "No available backend",
                "model": model,
                "strategy": strategy,
                "timestamp": time.time(),
            }

        # Update session affinity
        if session_id:
            self.session_map[session_id] = backend.id
            self._session_timestamps[session_id] = time.time()

        # Increment active request counter
        backend.active_requests += 1

        routing_info = {
            "backend_id": backend.id,
            "backend_name": backend.name,
            "engine": backend.engine,
            "url": backend.url,
            "score": {
                "ttft_ms": round(backend.score.ttft_ms, 2),
                "throughput_tps": round(backend.score.throughput_tps, 1),
                "error_rate": round(backend.score.error_rate, 4),
            },
            "strategy": strategy or "default",
            "timestamp": time.time(),
        }

        return backend, routing_info

    # --- Result Recording ---

    def record_result(
        self,
        backend_id: str,
        success: bool,
        ttft_ms: float = 0,
        tps: float = 0,
        tokens: int = 0,
    ):
        """
        Record a request result to update backend scores and stats.

        MUST be called after each completed request.
        """
        # Decrement active counter
        backend = self.registry.get_backend(backend_id)
        if backend and backend.active_requests > 0:
            backend.active_requests -= 1

        # Record to registry scoring
        self.registry.record_result(backend_id, success, ttft_ms, tps, tokens)

        if success:
            self.successful_requests += 1
        else:
            self.failed_requests += 1

    def release_backend(self, backend_id: str):
        """Release active request count without recording score (e.g., cancelled request)."""
        backend = self.registry.get_backend(backend_id)
        if backend and backend.active_requests > 0:
            backend.active_requests -= 1

    # --- Status & Metrics ---

    def get_status(self) -> dict:
        """Get comprehensive scheduler status."""
        elapsed = time.time() - self.start_time if self.start_time else 0
        return {
            "scheduler": {
                "uptime_seconds": round(elapsed, 1),
                "total_requests": self.total_requests,
                "successful_requests": self.successful_requests,
                "failed_requests": self.failed_requests,
                "success_rate": round(
                    self.successful_requests / max(self.total_requests, 1), 4
                ),
                "active_sessions": len(self.session_map),
            },
            "backends": self.registry.get_all_status(),
        }

    def get_metrics(self) -> dict:
        """Get Prometheus-compatible metrics snapshot."""
        return {
            "scheduler": {
                "total_requests": self.total_requests,
                "successful_requests": self.successful_requests,
                "failed_requests": self.failed_requests,
                "active_sessions": len(self.session_map),
            },
            "registry": self.registry.get_metrics(),
        }

    # --- Session Management ---

    def _clean_sessions(self):
        """Remove expired session entries."""
        now = time.time()
        expired = [
            sid for sid, ts in self._session_timestamps.items()
            if now - ts > self.session_ttl
        ]
        for sid in expired:
            del self.session_map[sid]
            del self._session_timestamps[sid]
        if expired:
            logger.debug(f"Cleaned {len(expired)} expired sessions")