"""
Routing Engine — selects the optimal backend per request based on:
  - Model-Aware: Only route to backends serving the requested model
  - Latency-Priority: Prefer lowest historical TTFT
  - Throughput-Priority: Prefer highest historical throughput
  - Cost-Aware: Prefer lowest cost_per_token
  - Affinity: Sticky session routing
"""

import logging
import random
from typing import List, Optional

from backend_registry import Backend, BackendRegistry

logger = logging.getLogger("router")


class RoutingEngine:
    """Decides which backend should handle a given inference request."""

    def __init__(self, registry: BackendRegistry, config: dict):
        self.registry = registry
        routing_cfg = config.get("routing", {})
        self.default_strategy = routing_cfg.get("default_strategy", "model_aware")
        strategies = routing_cfg.get("strategies", {})
        self.strategy_enabled = {
            "model_aware": strategies.get("model_aware", {}).get("enabled", True),
            "latency": strategies.get("latency", {}).get("enabled", True),
            "throughput": strategies.get("throughput", {}).get("enabled", True),
            "cost": strategies.get("cost", {}).get("enabled", True),
            "affinity": strategies.get("affinity", {}).get("enabled", True),
        }

    def select_backend(
        self,
        model: str,
        strategy: Optional[str] = None,
        session_id: Optional[str] = None,
        session_map: Optional[dict] = None,
    ) -> Optional[Backend]:
        """
        Select the best backend for a given request.

        Args:
            model: Requested model name.
            strategy: Explicit routing strategy override (latency|throughput|cost).
            session_id: Optional session identifier for affinity routing.
            session_map: Dict mapping session_id → backend_id.

        Returns:
            Selected Backend, or None if no backend available.
        """
        strategy = strategy or self.default_strategy

        # 1. Apply model-aware filter
        candidates = self.registry.get_backends_for_model(model)

        # 2. Filter by health + concurrency capacity
        candidates = [b for b in candidates if b.can_accept_request()]

        # 3. Check circuit breaker HALF_OPEN status
        # (HALF_OPEN backends are filtered in can_accept_request only if OPEN)

        if not candidates:
            logger.warning(f"No healthy backends available for model '{model}'")
            return None

        # 4. Affinity routing (if enabled and session_id provided)
        if self.strategy_enabled["affinity"] and session_id and session_map:
            backend_id = session_map.get(session_id)
            if backend_id:
                backend = self.registry.get_backend(backend_id)
                if backend and backend in candidates:
                    logger.debug(f"Affinity: session '{session_id}' → {backend.id}")
                    return backend

        # 5. Strategy-based selection
        if strategy == "latency" and self.strategy_enabled["latency"]:
            return self._select_by_latency(candidates)
        elif strategy == "throughput" and self.strategy_enabled["throughput"]:
            return self._select_by_throughput(candidates)
        elif strategy == "cost" and self.strategy_enabled["cost"]:
            return self._select_by_cost(candidates)
        else:
            return self._select_weighted(candidates)

    # --- Selection Strategies ---

    @staticmethod
    def _select_by_latency(candidates: List[Backend]) -> Optional[Backend]:
        """Pick backend with lowest average TTFT."""
        best = min(
            candidates,
            key=lambda b: b.score.ttft_ms if b.score.ttft_ms > 0 else float("inf")
        )
        logger.debug(f"Latency routing: {best.id} (TTFT={best.score.ttft_ms:.1f}ms)")
        return best

    @staticmethod
    def _select_by_throughput(candidates: List[Backend]) -> Optional[Backend]:
        """Pick backend with highest average throughput."""
        best = max(candidates, key=lambda b: b.score.throughput_tps)
        logger.debug(f"Throughput routing: {best.id} (TPS={best.score.throughput_tps:.1f})")
        return best

    @staticmethod
    def _select_by_cost(candidates: List[Backend]) -> Optional[Backend]:
        """Pick backend with lowest cost per token."""
        best = min(candidates, key=lambda b: b.cost_per_token)
        logger.debug(f"Cost routing: {best.id} (cost={best.cost_per_token})")
        return best

    @staticmethod
    def _select_weighted(candidates: List[Backend]) -> Optional[Backend]:
        """Weighted random selection (default for model_aware strategy)."""
        total_weight = sum(b.weight for b in candidates)
        if total_weight == 0:
            return random.choice(candidates) if candidates else None
        r = random.uniform(0, total_weight)
        cumulative = 0.0
        for backend in candidates:
            cumulative += backend.weight
            if r <= cumulative:
                logger.debug(f"Weighted routing: {backend.id} (weight={backend.weight})")
                return backend
        return candidates[-1]