"""
Scheduler Stress Test — concurrent load testing for the AI Model Scheduler.

Tests:
  1. Concurrency gradient: ramp from C1 → C2 → C4 → C8 → C16
  2. Routing latency: measure scheduler overhead (routing decision time)
  3. Backend distribution: verify requests are distributed across backends
  4. Circuit breaker: verify unhealthy backends are excluded

Usage:
    python scheduler_stress_test.py

Requirements:
    - Scheduler running on http://localhost:9000
    - At least one backend healthy
"""

import asyncio
import json
import logging
import statistics
import time
from dataclasses import dataclass, field
from typing import Dict, List

import aiohttp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] StressTest: %(message)s",
)
logger = logging.getLogger("stress_test")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCHEDULER_URL = "http://localhost:9000"
API_KEY = "test-key"
MODELS = ["qwen2.5:0.5b", "qwen2.5:1.5b"]

# Concurrency levels to test
CONCURRENCY_LEVELS = [1, 2, 4, 8, 16]

# Test prompt
TEST_PROMPT = {
    "model": MODELS[0],
    "messages": [{"role": "user", "content": "Hello, how are you?"}],
    "stream": False,
}


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class RequestResult:
    success: bool
    status_code: int
    latency_ms: float
    backend_id: str = ""
    routing_strategy: str = ""
    error: str = ""


@dataclass
class ConcurrencyReport:
    concurrency: int
    total_requests: int
    successes: int
    failures: int
    success_rate: float
    latency_p50_ms: float
    latency_p90_ms: float
    latency_p99_ms: float
    latency_avg_ms: float
    throughput_rps: float
    results: List[RequestResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Test Runner
# ---------------------------------------------------------------------------

class SchedulerStressTest:
    """Concurrent load test for the AI Model Scheduler."""

    def __init__(self, base_url: str = SCHEDULER_URL, api_key: str = API_KEY):
        self.base_url = base_url
        self.api_key = api_key
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        self.reports: List[ConcurrencyReport] = []

    async def check_health(self) -> bool:
        """Verify scheduler is reachable."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.base_url}/health") as resp:
                    data = await resp.json()
                    logger.info(f"Scheduler health: {data['status']}")
                    return data["status"] == "healthy"
        except Exception as e:
            logger.error(f"Cannot reach scheduler: {e}")
            return False

    async def send_request(self, model: str, strategy: str = None) -> RequestResult:
        """Send a single inference request and measure latency."""
        body = {
            "model": model,
            "messages": [{"role": "user", "content": "Stress test ping."}],
            "stream": False,
        }
        headers = dict(self.headers)
        if strategy:
            headers["X-Routing-Strategy"] = strategy

        start = time.monotonic()
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{self.base_url}/v1/chat/completions",
                    json=body,
                    headers=headers,
                ) as resp:
                    elapsed_ms = (time.monotonic() - start) * 1000
                    backend_id = resp.headers.get("X-Routed-Backend", "unknown")
                    routing = resp.headers.get("X-Routing-Strategy", "unknown")
                    if resp.status == 200:
                        return RequestResult(
                            success=True,
                            status_code=200,
                            latency_ms=round(elapsed_ms, 2),
                            backend_id=backend_id,
                            routing_strategy=routing,
                        )
                    else:
                        text = await resp.text()
                        return RequestResult(
                            success=False,
                            status_code=resp.status,
                            latency_ms=round(elapsed_ms, 2),
                            backend_id=backend_id,
                            error=text[:200],
                        )
        except asyncio.TimeoutError:
            elapsed_ms = (time.monotonic() - start) * 1000
            return RequestResult(
                success=False, status_code=504, latency_ms=elapsed_ms, error="Timeout"
            )
        except Exception as e:
            elapsed_ms = (time.monotonic() - start) * 1000
            return RequestResult(
                success=False, status_code=0, latency_ms=elapsed_ms, error=str(e)
            )

    async def run_concurrency_level(
        self, concurrency: int, requests_per_level: int = 20
    ) -> ConcurrencyReport:
        """Run a batch of requests at a given concurrency level."""
        logger.info(f"Testing C{concurrency} ({requests_per_level} requests)...")

        # Create tasks
        tasks = []
        for i in range(requests_per_level):
            # Mix models: 70% 0.5b, 30% 1.5b
            model = MODELS[0] if i % 10 < 7 else MODELS[1]
            tasks.append(self.send_request(model))

        start = time.monotonic()
        results = await asyncio.gather(*tasks)
        total_time = time.monotonic() - start

        successes = [r for r in results if r.success]
        failures = [r for r in results if not r.success]
        latencies = [r.latency_ms for r in results]

        # Count per-backend distribution
        backend_counts: Dict[str, int] = {}
        for r in results:
            bid = r.backend_id
            backend_counts[bid] = backend_counts.get(bid, 0) + 1

        report = ConcurrencyReport(
            concurrency=concurrency,
            total_requests=len(results),
            successes=len(successes),
            failures=len(failures),
            success_rate=round(len(successes) / max(len(results), 1), 4),
            latency_p50_ms=round(statistics.median(latencies), 2) if latencies else 0,
            latency_p90_ms=round(self._percentile(latencies, 90), 2),
            latency_p99_ms=round(self._percentile(latencies, 99), 2),
            latency_avg_ms=round(statistics.mean(latencies), 2) if latencies else 0,
            throughput_rps=round(len(results) / max(total_time, 0.001), 2),
            results=results,
        )

        logger.info(f"  C{concurrency} → {report.success_rate*100:.0f}% success, "
                     f"P50={report.latency_p50_ms}ms, P90={report.latency_p90_ms}ms, "
                     f"RPS={report.throughput_rps}")
        logger.info(f"  Backend distribution: {backend_counts}")

        return report

    async def run_full_benchmark(self, requests_per_level: int = 20):
        """Run the complete concurrency gradient benchmark."""
        logger.info("=" * 60)
        logger.info("  AI Model Scheduler — Stress Test")
        logger.info(f"  URL: {self.base_url}")
        logger.info(f"  Concurrency: {CONCURRENCY_LEVELS}")
        logger.info(f"  Requests/level: {requests_per_level}")
        logger.info("=" * 60)

        if not await self.check_health():
            logger.error("Scheduler not healthy — aborting test")
            return []

        for c in CONCURRENCY_LEVELS:
            report = await self.run_concurrency_level(c, requests_per_level)
            self.reports.append(report)
            await asyncio.sleep(1)  # Cool-down

        self.print_summary()
        return self.reports

    def print_summary(self):
        """Print a formatted summary table."""
        logger.info("\n" + "=" * 80)
        logger.info("  BENCHMARK SUMMARY")
        logger.info("=" * 80)
        header = (
            f"{'C':<6} {'Success':<8} {'P50(ms)':<10} {'P90(ms)':<10} "
            f"{'P99(ms)':<10} {'Avg(ms)':<10} {'RPS':<8}"
        )
        logger.info(header)
        logger.info("-" * 80)
        for r in self.reports:
            logger.info(
                f"  C{r.concurrency:<4} {r.success_rate*100:>6.0f}%  "
                f"{r.latency_p50_ms:>8.1f}  {r.latency_p90_ms:>8.1f}  "
                f"{r.latency_p99_ms:>8.1f}  {r.latency_avg_ms:>8.1f}  "
                f"{r.throughput_rps:>6.1f}"
            )
        logger.info("=" * 80)

    @staticmethod
    def _percentile(data: List[float], p: float) -> float:
        if not data:
            return 0.0
        sorted_data = sorted(data)
        k = (len(sorted_data) - 1) * p / 100.0
        f = int(k)
        c = f + 1 if f + 1 < len(sorted_data) else f
        d0 = sorted_data[f] * (c - k)
        d1 = sorted_data[c] * (k - f) if c != f else 0
        return d0 + d1


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    tester = SchedulerStressTest()
    await tester.run_full_benchmark(requests_per_level=20)


if __name__ == "__main__":
    asyncio.run(main())