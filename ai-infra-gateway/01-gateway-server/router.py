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


def decide(snap: Optional[dict], token_count: int, cache_hit_prob: float) -> Tuple[Optional[dict], Optional[str], List[str]]:
    """按快照规则顺序匹配 (已按 priority 降序), 返回 (action, rule_id, reasons)

    action: {"tier": "vllm-3b-service", "fallbackChain": [...]} 或 None
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
            return rule.get("action"), rule.get("id"), reasons
        reasons.append(f"规则 {rule.get('id')} 未命中")
    reasons.append("无规则命中 → 走默认路由")
    return None, None, reasons
