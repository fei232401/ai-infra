"""
Backend Registry — manages inference backend lifecycle:
  - Registration: Load backends from YAML config
  - Health Check: Periodic HTTP probes with circuit breaker logic
  - Scoring: Track latency / throughput / error rate per backend
  - Discovery: Query backends by model, strategy, health status
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

import aiohttp
import yaml

logger = logging.getLogger("backend_registry")


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

class BackendHealth(Enum):
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class CircuitState(Enum):
    CLOSED = "closed"            # normal operation
    OPEN = "open"                # failing, reject all requests
    HALF_OPEN = "half_open"      # testing the waters


@dataclass
class BackendScore:
    """Rolling window performance metrics for a backend."""
    ttft_ms: float = 0.0
    throughput_tps: float = 0.0
    error_rate: float = 0.0
    success_count: int = 0
    error_count: int = 0
    total_tokens: int = 0
    window_size: int = 20
    _ttft_samples: List[float] = field(default_factory=list)
    _tps_samples: List[float] = field(default_factory=list)
    _window_errors: List[bool] = field(default_factory=list)

    def record_success(self, ttft_ms: float, tps: float, tokens: int):
        self._ttft_samples.append(ttft_ms)
        self._tps_samples.append(tps)
        self._window_errors.append(False)
        self.success_count += 1
        self.total_tokens += tokens
        self._trim_window()
        self._recalc()

    def record_error(self):
        self._window_errors.append(True)
        self.error_count += 1
        self._trim_window()
        self._recalc()

    def _trim_window(self):
        while len(self._ttft_samples) > self.window_size:
            self._ttft_samples.pop(0)
        while len(self._tps_samples) > self.window_size:
            self._tps_samples.pop(0)
        while len(self._window_errors) > self.window_size:
            self._window_errors.pop(0)

    def _recalc(self):
        if self._ttft_samples:
            self.ttft_ms = sum(self._ttft_samples) / len(self._ttft_samples)
        if self._tps_samples:
            self.throughput_tps = sum(self._tps_samples) / len(self._tps_samples)
        if self._window_errors:
            self.error_rate = sum(self._window_errors) / len(self._window_errors)


@dataclass
class Backend:
    """Represents one inference backend endpoint."""
    id: str
    name: str
    url: str
    api_path: str
    chat_path: str
    health_path: str
    models: List[str]
    engine: str
    cost_per_token: float = 0.0
    max_concurrency: int = 1
    weight: float = 1.0
    tags: List[str] = field(default_factory=list)

    # Runtime state
    health: BackendHealth = BackendHealth.UNKNOWN
    circuit_state: CircuitState = CircuitState.CLOSED
    circuit_state_changed_at: float = 0.0
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    active_requests: int = 0
    score: BackendScore = field(default_factory=BackendScore)
    last_health_check: float = 0.0
    last_error: str = ""

    def can_accept_request(self) -> bool:
        if self.health != BackendHealth.HEALTHY:
            return False
        if self.circuit_state == CircuitState.OPEN:
            return False
        if self.active_requests >= self.max_concurrency:
            return False
        return True

    def has_model(self, model_name: str) -> bool:
        return model_name in self.models


# ---------------------------------------------------------------------------
# Backend Registry
# ---------------------------------------------------------------------------

class BackendRegistry:
    """Central registry for all inference backends."""

    def __init__(self, config: dict):
        self.config = config
        scheduler_cfg = config.get("scheduler", {})
        self.health_check_interval = scheduler_cfg.get("health_check_interval", 10)
        self.health_check_timeout = scheduler_cfg.get("health_check_timeout", 5)
        self.unhealthy_threshold = scheduler_cfg.get("backend_unhealthy_threshold", 3)
        self.recovery_threshold = scheduler_cfg.get("backend_recovery_threshold", 2)
        self.score_window_size = scheduler_cfg.get("score_window_size", 20)

        cb_cfg = scheduler_cfg.get("circuit_breaker", {})
        self.cb_failure_threshold = cb_cfg.get("failure_threshold", 3)
        self.cb_timeout = cb_cfg.get("timeout", 30)
        self.cb_half_open_max = cb_cfg.get("half_open_max_requests", 2)

        self.backends: Dict[str, Backend] = {}
        self._http_session: Optional[aiohttp.ClientSession] = None
        self._health_check_task: Optional[asyncio.Task] = None
        self._running = False
        self._lock = asyncio.Lock()

    # --- Initialization ---

    def load_from_config(self):
        backend_list = self.config.get("backends", [])
        for b in backend_list:
            backend = Backend(
                id=b["id"],
                name=b["name"],
                url=b["url"],
                api_path=b["api_path"],
                chat_path=b["chat_path"],
                health_path=b["health_path"],
                models=b.get("models", []),
                engine=b.get("engine", "ollama"),
                cost_per_token=b.get("cost_per_token", 0.0),
                max_concurrency=b.get("max_concurrency", 1),
                weight=b.get("weight", 1.0),
                tags=b.get("tags", []),
            )
            backend.score.window_size = self.score_window_size
            self.backends[backend.id] = backend
            logger.info(f"Registered backend: {backend.id} ({backend.name})")
        logger.info(f"Loaded {len(self.backends)} backends from config")

    async def start(self):
        self._http_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.health_check_timeout)
        )
        self._running = True
        self._health_check_task = asyncio.create_task(self._health_check_loop())
        logger.info(f"Backend registry started (health check every {self.health_check_interval}s)")

    async def stop(self):
        self._running = False
        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass
        if self._http_session:
            await self._http_session.close()
        logger.info("Backend registry stopped")

    # --- Health Check Loop ---

    async def _health_check_loop(self):
        while self._running:
            await self._check_all_backends()
            await asyncio.sleep(self.health_check_interval)

    async def _check_all_backends(self):
        tasks = [self._check_one_backend(b) for b in self.backends.values()]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _check_one_backend(self, backend: Backend):
        url = f"{backend.url}{backend.health_path}"
        try:
            async with self._http_session.get(url) as resp:
                if resp.status < 500:
                    await self._mark_healthy(backend)
                else:
                    await self._mark_unhealthy(backend, f"Health check returned {resp.status}")
        except asyncio.TimeoutError:
            await self._mark_unhealthy(backend, "Health check timeout")
        except aiohttp.ClientError as e:
            await self._mark_unhealthy(backend, f"Connection error: {e}")
        except Exception as e:
            await self._mark_unhealthy(backend, f"Unexpected error: {e}")
        finally:
            backend.last_health_check = time.time()

    async def _mark_healthy(self, backend: Backend):
        async with self._lock:
            backend.consecutive_failures = 0
            backend.consecutive_successes += 1
            if backend.consecutive_successes >= self.recovery_threshold:
                backend.health = BackendHealth.HEALTHY
                backend.last_error = ""
            # Circuit breaker recovery
            if backend.circuit_state == CircuitState.HALF_OPEN:
                if backend.consecutive_successes >= self.cb_half_open_max:
                    backend.circuit_state = CircuitState.CLOSED
                    backend.circuit_state_changed_at = time.time()
                    logger.info(f"Circuit breaker CLOSED for {backend.id} (recovered)")

    async def _mark_unhealthy(self, backend: Backend, error: str):
        async with self._lock:
            backend.consecutive_successes = 0
            backend.consecutive_failures += 1
            backend.last_error = error
            if backend.consecutive_failures >= self.unhealthy_threshold:
                backend.health = BackendHealth.UNHEALTHY
            # Circuit breaker
            if backend.consecutive_failures >= self.cb_failure_threshold:
                if backend.circuit_state == CircuitState.CLOSED:
                    backend.circuit_state = CircuitState.OPEN
                    backend.circuit_state_changed_at = time.time()
                    logger.warning(f"Circuit breaker OPEN for {backend.id}: {error}")

    # --- Circuit Breaker Management ---

    async def try_half_open(self, backend: Backend) -> bool:
        """Attempt to transition a backend from OPEN to HALF_OPEN."""
        async with self._lock:
            now = time.time()
            if backend.circuit_state != CircuitState.OPEN:
                return True
            if now - backend.circuit_state_changed_at >= self.cb_timeout:
                backend.circuit_state = CircuitState.HALF_OPEN
                backend.circuit_state_changed_at = now
                backend.consecutive_successes = 0
                logger.info(f"Circuit breaker HALF_OPEN for {backend.id} (testing)")
                return True
            return False

    def record_result(self, backend_id: str, success: bool, ttft_ms: float = 0, tps: float = 0, tokens: int = 0):
        """Record a request result to update backend score."""
        backend = self.backends.get(backend_id)
        if not backend:
            return
        if success:
            backend.score.record_success(ttft_ms, tps, tokens)
            backend.consecutive_failures = 0
        else:
            backend.score.record_error()
            backend.consecutive_failures += 1
            if backend.consecutive_failures >= self.cb_failure_threshold:
                if backend.circuit_state == CircuitState.CLOSED:
                    backend.circuit_state = CircuitState.OPEN
                    backend.circuit_state_changed_at = time.time()
                    logger.warning(f"Circuit breaker OPEN for {backend_id} (request failure)")

    # --- Query Methods (used by Router) ---

    def get_backend(self, backend_id: str) -> Optional[Backend]:
        return self.backends.get(backend_id)

    def get_healthy_backends(self) -> List[Backend]:
        return [b for b in self.backends.values() if b.health == BackendHealth.HEALTHY]

    def get_backends_for_model(self, model_name: str) -> List[Backend]:
        """Return healthy backends that serve the given model."""
        return [
            b for b in self.get_healthy_backends()
            if b.has_model(model_name)
        ]

    def get_all_status(self) -> List[dict]:
        """Return summary status for all backends (for dashboard/API)."""
        results = []
        for b in self.backends.values():
            results.append({
                "id": b.id,
                "name": b.name,
                "health": b.health.value,
                "circuit_state": b.circuit_state.value,
                "engine": b.engine,
                "models": b.models,
                "active_requests": b.active_requests,
                "max_concurrency": b.max_concurrency,
                "cost_per_token": b.cost_per_token,
                "score": {
                    "ttft_ms": round(b.score.ttft_ms, 2),
                    "throughput_tps": round(b.score.throughput_tps, 1),
                    "error_rate": round(b.score.error_rate, 4),
                    "success_count": b.score.success_count,
                    "error_count": b.score.error_count,
                    "total_tokens": b.score.total_tokens,
                },
                "last_error": b.last_error,
                "last_health_check": b.last_health_check,
            })
        return results

    def get_metrics(self) -> dict:
        """Return Prometheus-compatible metrics snapshot."""
        return {
            "backend_count_total": len(self.backends),
            "backend_healthy_count": len([b for b in self.backends.values() if b.health == BackendHealth.HEALTHY]),
            "backend_unhealthy_count": len([b for b in self.backends.values() if b.health == BackendHealth.UNHEALTHY]),
            "backends": {
                b.id: {
                    "health": b.health.value,
                    "circuit_state": b.circuit_state.value,
                    "active_requests": b.active_requests,
                    "ttft_ms": round(b.score.ttft_ms, 2),
                    "throughput_tps": round(b.score.throughput_tps, 1),
                    "error_rate": round(b.score.error_rate, 4),
                }
                for b in self.backends.values()
            }
        }


# ---------------------------------------------------------------------------
# Config Loader
# ---------------------------------------------------------------------------

def load_config(config_path: str = "01-scheduler-core/config/scheduler_config.yaml") -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)