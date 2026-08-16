"""cache-aware 动态加权单元测试

测试对象: router.decide / _apply_dynamic_decision 的命中率打折逻辑
快照 tiers[].cacheHitRatio 由 Operator 从 vLLM prefix_cache 指标实采,
gateway 决策时用它打折预判 P99 — 高命中 tier 不易被降级走 (BLUEPRINT §5.1)
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from router import decide


def _snap(tiers, slo=None):
    """构造测试快照: 一条 HIGH 复杂度规则, 命中率打折只影响动态降级判定"""
    return {
        "version": "1",
        "policyName": "test",
        "rules": [
            {
                "id": "r1",
                "priority": 100,
                "condition": {"complexity": "high"},
                "action": {"tier": "vllm-3b-service", "fallbackChain": ["ollama-service"]},
            }
        ],
        "slo": {"low": 200, "medium": 1000, "high": slo or 5000},
        "tiers": tiers,
    }


class TestCacheAwareDowngrade:

    def test_high_hit_keeps_tier_when_raw_p99_over_slo(self):
        """命中率高 → 有效 P99 打折 → tier 不再过载 → 不降级 (动态加权生效)"""
        snap = _snap([
            {"name": "vllm-3b-service", "healthy": True, "predictedP99Ms": 6000, "cacheHitRatio": 0.95},
            {"name": "ollama-service", "healthy": True, "predictedP99Ms": 300},
        ])
        action, _, reasons = decide(snap, token_count=2000, cache_hit_prob=0.0)
        # 6000 × (1-0.95×0.5) = 3150 ≤ SLO 5000 → 保持 vllm
        assert action["tier"] == "vllm-3b-service"
        assert any("cache-aware" in r for r in reasons)

    def test_zero_hit_downgrades_when_raw_p99_over_slo(self):
        """命中率 0 → 打折无效 → 行为与旧版一致 → 预判超 SLO 降级"""
        snap = _snap([
            {"name": "vllm-3b-service", "healthy": True, "predictedP99Ms": 6000, "cacheHitRatio": 0.0},
            {"name": "ollama-service", "healthy": True, "predictedP99Ms": 300},
        ])
        action, _, _ = decide(snap, token_count=2000, cache_hit_prob=0.0)
        assert action["tier"] == "ollama-service"
        assert action.get("downgraded_from") == "vllm-3b-service"

    def test_fallback_tier_high_hit_allows_downgrade(self):
        """fallback tier 命中率高 → 其有效 P99 打折 → 可作为降级目标"""
        snap = _snap([
            {"name": "vllm-3b-service", "healthy": True, "predictedP99Ms": 6000, "cacheHitRatio": 0.0},
            {"name": "ollama-service", "healthy": True, "predictedP99Ms": 9000, "cacheHitRatio": 0.9},
        ])
        action, _, reasons = decide(snap, token_count=2000, cache_hit_prob=0.0)
        # ollama 原始 9000 > SLO, 但 9000 × (1-0.9×0.5) = 4950 ≤ 5000 → 可降级
        assert action["tier"] == "ollama-service"
        assert any("打折后" in r for r in reasons)

    def test_missing_cache_hit_key_backward_compat(self):
        """旧快照无 cacheHitRatio 字段 → 命中率视为 0 → 行为与旧版一致"""
        snap = _snap([
            {"name": "vllm-3b-service", "healthy": True, "predictedP99Ms": 6000},
            {"name": "ollama-service", "healthy": True, "predictedP99Ms": 300},
        ])
        action, _, _ = decide(snap, token_count=2000, cache_hit_prob=0.0)
        assert action["tier"] == "ollama-service"

    def test_boost_zero_disables_discount(self):
        """cache_boost=0 → 关闭打折, 命中率不再影响降级判定"""
        snap = _snap([
            {"name": "vllm-3b-service", "healthy": True, "predictedP99Ms": 6000, "cacheHitRatio": 0.95},
            {"name": "ollama-service", "healthy": True, "predictedP99Ms": 300},
        ])
        action, _, _ = decide(snap, token_count=2000, cache_hit_prob=0.0, cache_boost=0.0)
        assert action["tier"] == "ollama-service"
