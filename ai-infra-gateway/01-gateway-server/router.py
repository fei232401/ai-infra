"""CyberRouter 数据面路由决策 — Python 实现
与 cyberrouter Go 包 (internal/decision) 保持语义一致:
  复杂度评估 → 规则匹配 (优先级降序) → 选择 tier → 可解释理由

快照由 Operator 编译 (不直接碰 CRD), gateway 只消费 ConfigMap 快照
"""
from typing import List, Optional, Tuple


def estimate_tokens(body: dict) -> int:
    """从 messages 估算 token 数 (Qwen2.5 中文 ~0.489 token/字符, 用 0.5 近似)"""
    msgs = body.get("messages", [])
    chars = sum(len(m.get("content", "")) for m in msgs if isinstance(m, dict))
    return int(chars * 0.5)


def complexity(token_count: int) -> str:
    """复杂度评估 (与 Go: low<100, medium<1000, else high)"""
    if token_count < 100:
        return "low"
    if token_count < 1000:
        return "medium"
    return "high"


def _parse(expr: str) -> Tuple[Optional[str], Optional[float]]:
    """解析比较表达式 "< 100" → ("<", 100.0)"""
    expr = expr.strip()
    for op in ("<=", ">=", "<", ">", "="):
        if expr.startswith(op):
            try:
                return op, float(expr[len(op):].strip())
            except ValueError:
                return None, None
    return None, None


def _compare(v, expr: str) -> bool:
    op, val = _parse(expr)
    if op is None or val is None:
        return False
    if op == "<":
        return v < val
    if op == "<=":
        return v <= val
    if op == ">":
        return v > val
    if op == ">=":
        return v >= val
    if op == "=":
        return v == val
    return False


def decide(snap: Optional[dict], token_count: int, cache_hit_prob: float, cache_boost: float = 0.5) -> Tuple[Optional[dict], Optional[str], List[str]]:
    """决策: 规则匹配 (静态) → 动态状态检查 + 预判性降级 (Phase 3)

    cache_boost: cache-aware 动态加权系数 (0=关闭, 0.5=满命中时有效 P99 减半),
                 由 gateway_config.yaml cache_aware.boost 传入 (启发式初值, 需真实负载标定)

    返回 (action, rule_id, reasons)
    action: {"tier": "...", "fallbackChain": [...]} 或 None (走默认路由)
    """
    if not snap:
        return None, None, ["快照未就绪 (Operator 未同步?)"]
    reasons = [f"复杂度评估: {token_count} tokens → {complexity(token_count)}"]
    for rule in snap.get("rules", []):
        cond = rule.get("condition", {})
        matched = True
        why = []
        if cond.get("complexity"):
            if complexity(token_count) == cond["complexity"]:
                why.append(f"complexity={cond['complexity']}")
            else:
                matched = False
        if cond.get("tokenCount") and matched:
            if _compare(token_count, cond["tokenCount"]):
                why.append(f"tokenCount={token_count} {cond['tokenCount']}")
            else:
                matched = False
        if cond.get("cacheHitProbability") and matched:
            if _compare(cache_hit_prob, cond["cacheHitProbability"]):
                why.append(f"cacheHitProbability={cache_hit_prob} {cond['cacheHitProbability']}")
            else:
                matched = False
        if matched:
            reasons.append(f"命中规则 {rule.get('id')} (priority {rule.get('priority')}): {' AND '.join(why)}")
            action = rule.get("action")
            # Phase 3: 动态状态检查 + 预判性降级 (GPU 忙/不健康 → 沿 fallback 降级)
            _apply_dynamic_decision(snap, action, token_count, reasons, cache_boost)
            return action, rule.get("id"), reasons
        reasons.append(f"规则 {rule.get('id')} 未命中")
    reasons.append("无规则命中 → 走默认路由")
    return None, None, reasons


def _tier_status(snap: dict, tier_name: str) -> Optional[dict]:
    """查快照 tiers 动态状态中某 tier 的状态 (Phase 3)"""
    for t in snap.get("tiers", []):
        if t.get("name") == tier_name:
            return t
    return None


def _apply_dynamic_decision(snap: dict, action: dict, token_count: int, reasons: List[str], cache_boost: float = 0.5) -> None:
    """预判性降级: 选中 tier 不健康 或 有效P99超SLO → 沿 fallbackChain 降级
    无动态状态 (旧快照/未采集) 时维持静态决策 (向后兼容)

    cache-aware 动态加权: 判定过载时用 tier 实测命中率打折预判 P99 —
    命中率高 → 缓存请求廉价 (免 prefill, 延迟低) → 有效 P99 低 → tier 更不易被降级走,
    等价于"高命中 tier 权重上调" (BLUEPRINT §5.1)。命中率 0 / 无该字段 → 不变化。
    """
    tier = action.get("tier")
    status = _tier_status(snap, tier)
    if status is None:
        return  # 快照无动态状态 → 静态决策
    # SLO 结构兼容: Operator 快照是 {"high": 800} (int), 兼容 dict 形式 {"high": {"maxP99Ms": 800}}
    slo_entry = snap.get("slo", {}).get(complexity(token_count), 5000)
    slo_ms = slo_entry if isinstance(slo_entry, (int, float)) else (slo_entry or {}).get("maxP99Ms", 5000)
    predicted = status.get("predictedP99Ms", 0)
    hit = status.get("cacheHitRatio", 0.0)
    effective_p99 = predicted * (1 - hit * cache_boost)
    if hit > 0:
        reasons.append(
            f"cache-aware: {tier} 命中率 {hit:.0%} → 有效P99 {predicted:.0f}×{1 - hit * cache_boost:.2f}≈{effective_p99:.0f}ms"
        )
    overloaded = (not status.get("healthy", True)) or (effective_p99 > slo_ms)

    if not overloaded:
        reasons.append(f"tier 状态 OK: {tier} 有效P99≈{effective_p99:.0f}ms ≤ SLO {slo_ms}ms")
        return

    reasons.append(
        f"⚠️ 预判降级: {tier} (healthy={status.get('healthy')}, 有效P99≈{effective_p99:.0f}ms > SLO {slo_ms}ms"
        + (f", 打折前 {predicted:.0f}ms)" if hit > 0 else ")")
    )
    for fb in action.get("fallbackChain", []):
        if fb == tier:
            continue
        fb_status = _tier_status(snap, fb)
        if fb_status is None:
            # 无动态状态 (未被 Operator 监控) → 视为可用 (乐观: 无证据不健康)
            reasons.append(f"  → 降级到 {fb} (无动态状态, 视为可用)")
            action["tier"] = fb
            action["downgraded_from"] = tier  # 供 gateway 决策指标 (H6)
            return
        fb_hit = fb_status.get("cacheHitRatio", 0.0)
        fb_effective = fb_status.get("predictedP99Ms", 0) * (1 - fb_hit * cache_boost)
        if fb_status.get("healthy", True) and fb_effective <= slo_ms:
            reasons.append(
                f"  → 降级到 {fb} (预判P99≈{fb_status.get('predictedP99Ms', 0):.0f}ms"
                + (f", 命中率 {fb_hit:.0%} 打折后 {fb_effective:.0f}ms ≤ SLO)" if fb_hit > 0 else " ≤ SLO)")
            )
            action["tier"] = fb
            action["downgraded_from"] = tier  # 供 gateway 决策指标 (H6)
            return
    reasons.append("  → fallback 均不可用, 保持原 tier")
