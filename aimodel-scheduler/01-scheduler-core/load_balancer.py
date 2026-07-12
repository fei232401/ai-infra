"""
Load Balancer — distributes requests across backend instances with adaptive scoring.

Strategies:
  - Weighted Round Robin: Static weights from config
  - Least Connections: Prefer backend with fewest active requests
  - Adaptive Score: Dynamic scoring based on latency + error rate + concurrency
"""

import logging
from typing import List, Optional

from backend_registry import Backend

logger = logging.getLogger("load_balancer")


class LoadBalancer:
    """Distributes requests across candidate backends."""

    def __init__(self, strategy: str = "adaptive"):
        self.strategy = strategy  # "weighted" | "least_conn" | "adaptive"
        self._rr_counters: dict = {}  # round-robin per model group

    def select(self, candidates: List[Backend], model: str = "") -> Optional[Backend]:
        """Select one backend from the candidate pool."""
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]

        if self.strategy == "least_conn":
            return self._least_connections(candidates)
        elif self.strategy == "adaptive":
            return self._adaptive_score(candidates)
        else:
            return self._weighted_round_robin(candidates, model)

    # --- Strategies ---

    @staticmethod
    def _least_connections(candidates: List[Backend]) -> Backend:
        """Prefer backend with fewest active requests."""
        best = min(candidates, key=lambda b: b.active_requests)
        logger.debug(f"Least-conn: {best.id} (active={best.active_requests})")
        return best

    @staticmethod
    def _weighted_round_robin(candidates: List[Backend], model: str) -> Backend:
        """Weighted round-robin: simple sequential with weight-based skipping."""
        # Simplified: just weighted random (more practical for async context)
        total_weight = sum(b.weight for b in candidates)
        if total_weight == 0:
            return candidates[0]

        import random
        r = random.uniform(0, total_weight)
        cumulative = 0.0
        for backend in candidates:
            cumulative += backend.weight
            if r <= cumulative:
                return backend
        return candidates[-1]

    @staticmethod
    def _adaptive_score(candidates: List[Backend]) -> Backend:
        """
        Dynamic scoring based on:
          score = weight * (1 - error_rate) / (ttft_ms_normalized * concurrency_penalty)

        Higher score = better candidate.
        """
        scores = []
        for b in candidates:
            # Normalize TTFT: 0 = best (treat unknown as median)
            ttft = b.score.ttft_ms if b.score.ttft_ms > 0 else 500.0
            ttft_norm = max(ttft, 1.0)  # avoid div by zero

            # Error penalty: 1.0 = no errors, 0.0 = all errors
            error_factor = max(1.0 - b.score.error_rate, 0.01)

            # Concurrency penalty: higher utilization = lower score
            if b.max_concurrency > 0:
                utilization = b.active_requests / b.max_concurrency
            else:
                utilization = 1.0
            concurrency_factor = max(1.0 - utilization * 0.5, 0.1)

            # Combined adaptive score
            score = b.weight * error_factor * concurrency_factor / ttft_norm
            scores.append((score, b))

        # Pick highest score
        scores.sort(key=lambda x: x[0], reverse=True)
        best = scores[0][1]
        logger.debug(
            f"Adaptive: {best.id} (score={scores[0][0]:.6f}, "
            f"ttft={best.score.ttft_ms:.1f}ms, "
            f"err={best.score.error_rate:.3f}, "
            f"active={best.active_requests}/{best.max_concurrency})"
        )
        return best