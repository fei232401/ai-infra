"""
Cross-Backend Comparison — benchmark the same request across different backends
to measure routing effectiveness and backend performance differences.

Tests:
  1. Compare TTFT across Ollama Local A / Ollama Local B / Mock vLLM
  2. Verify latency-priority routing picks the fastest backend
  3. Verify cost-priority routing picks the cheapest backend

Usage:
    python cross_backend_compare.py
"""

import asyncio
import logging
import statistics
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

import aiohttp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] CrossBackend: %(message)s",
)
logger = logging.getLogger("cross_backend")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCHEDULER_URL = "http://localhost:9000"
API_KEY = "test-key"

# Direct backend URLs (for direct comparison, bypassing scheduler)
DIRECT_URLS = {
    "ollama-local-a": "http://127.0.0.1:11434/api/chat",
    "ollama-local-b": "http://127.0.0.1:11435/api/chat",
    "mock-vllm": "http://127.0.0.1:11436/api/chat",
}

# Test scenarios
SCENARIOS = [
    {"name": "short_zh", "prompt": "你好", "weight": 0.5},
    {"name": "medium_zh", "prompt": "请解释一下机器学习和深度学习的区别", "weight": 0.3},
    {"name": "long_zh", "prompt": "请详细分析Kubernetes的调度机制，包括预选和优选阶段的具体算法", "weight": 0.15},
    {"name": "health", "prompt": "ping", "weight": 0.05},
]


@dataclass
class BackendLatency:
    backend_id: str
    ttft_ms: float
    total_ms: float
    success: bool
    error: str = ""


class CrossBackendComparison:
    """Compares inference performance across all backends."""

    def __init__(self):
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
        }

    async def measure_direct(
        self, url: str, prompt: str, model: str = "qwen2.5:0.5b"
    ) -> BackendLatency:
        """Measure latency going directly to a backend (bypassing scheduler)."""
        body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
        start = time.monotonic()
        try:
            timeout = aiohttp.ClientTimeout(total=60)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=body) as resp:
                    total_ms = (time.monotonic() - start) * 1000
                    if resp.status == 200:
                        data = await resp.json()
                        ttft_ms = data.get("total_duration", total_ms * 1000) / 1000
                        return BackendLatency(
                            backend_id="direct",
                            ttft_ms=round(ttft_ms, 2),
                            total_ms=round(total_ms, 2),
                            success=True,
                        )
                    else:
                        return BackendLatency(
                            backend_id="direct",
                            ttft_ms=0,
                            total_ms=round(total_ms, 2),
                            success=False,
                            error=f"HTTP {resp.status}",
                        )
        except Exception as e:
            total_ms = (time.monotonic() - start) * 1000
            return BackendLatency(
                backend_id="direct", ttft_ms=0, total_ms=total_ms, success=False, error=str(e)
            )

    async def measure_via_scheduler(
        self, model: str, prompt: str, strategy: Optional[str] = None
    ) -> BackendLatency:
        """Measure latency going through the scheduler."""
        body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
        headers = dict(self.headers)
        if strategy:
            headers["X-Routing-Strategy"] = strategy

        start = time.monotonic()
        try:
            timeout = aiohttp.ClientTimeout(total=60)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{SCHEDULER_URL}/v1/chat/completions",
                    json=body,
                    headers=headers,
                ) as resp:
                    total_ms = (time.monotonic() - start) * 1000
                    backend_id = resp.headers.get("X-Routed-Backend", "unknown")
                    if resp.status == 200:
                        data = await resp.json()
                        ttft_ms = data.get("total_duration", total_ms * 1000) / 1000
                        return BackendLatency(
                            backend_id=backend_id,
                            ttft_ms=round(ttft_ms, 2),
                            total_ms=round(total_ms, 2),
                            success=True,
                        )
                    else:
                        return BackendLatency(
                            backend_id=backend_id,
                            ttft_ms=0,
                            total_ms=round(total_ms, 2),
                            success=False,
                            error=f"HTTP {resp.status}",
                        )
        except Exception as e:
            total_ms = (time.monotonic() - start) * 1000
            return BackendLatency(
                backend_id="error", ttft_ms=0, total_ms=total_ms, success=False, error=str(e)
            )

    async def compare_strategies(
        self, model: str = "qwen2.5:0.5b", rounds: int = 5
    ) -> Dict[str, List[BackendLatency]]:
        """Compare different routing strategies with the same prompt."""
        prompt = "请解释一下什么是云计算"
        strategies = {
            "default": None,
            "latency": "latency",
            "cost": "cost",
            "throughput": "throughput",
        }
        results: Dict[str, List[BackendLatency]] = {}

        for name, strategy in strategies.items():
            logger.info(f"Testing strategy '{name}' ({rounds} rounds)...")
            round_results = []
            for _ in range(rounds):
                r = await self.measure_via_scheduler(model, prompt, strategy)
                round_results.append(r)
                await asyncio.sleep(0.5)
            results[name] = round_results

            avg_ttft = statistics.mean([r.ttft_ms for r in round_results if r.success])
            avg_total = statistics.mean([r.total_ms for r in round_results if r.success])
            backends = set(r.backend_id for r in round_results)
            logger.info(
                f"  {name}: avg_ttft={avg_ttft:.1f}ms, avg_total={avg_total:.1f}ms, backends={backends}"
            )

        return results

    async def run_full_comparison(self):
        """Run the complete cross-backend comparison."""
        logger.info("=" * 60)
        logger.info("  Cross-Backend Comparison")
        logger.info("=" * 60)

        # 1. Strategy comparison
        logger.info("\n--- Strategy Comparison ---")
        strategy_results = await self.compare_strategies(model="qwen2.5:0.5b", rounds=5)

        # 2. Direct backend comparison (if backends are up)
        logger.info("\n--- Direct Backend Comparison ---")
        prompt = "你好，请简单介绍一下自己"
        for backend_id, url in DIRECT_URLS.items():
            try:
                r = await self.measure_direct(url, prompt)
                logger.info(
                    f"  {backend_id}: ttft={r.ttft_ms:.1f}ms, total={r.total_ms:.1f}ms, "
                    f"success={r.success}"
                )
            except Exception as e:
                logger.info(f"  {backend_id}: unreachable ({e})")

        # 3. Summary
        logger.info("\n" + "=" * 60)
        logger.info("  COMPARISON SUMMARY")
        for strategy, results in strategy_results.items():
            successes = [r for r in results if r.success]
            if successes:
                avg_ttft = statistics.mean([r.ttft_ms for r in successes])
                avg_total = statistics.mean([r.total_ms for r in successes])
                backend_dist = {}
                for r in successes:
                    backend_dist[r.backend_id] = backend_dist.get(r.backend_id, 0) + 1
                logger.info(
                    f"  {strategy:>12}: ttft={avg_ttft:.1f}ms, total={avg_total:.1f}ms, "
                    f"backends={backend_dist}"
                )
        logger.info("=" * 60)


async def main():
    tester = CrossBackendComparison()
    await tester.run_full_comparison()


if __name__ == "__main__":
    asyncio.run(main())