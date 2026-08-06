// Package decision — 决策引擎核心 (纯逻辑, 可单元测试)
// 职责: 复杂度评估 → 规则匹配 → 选择 tier → 输出可解释决策 (M2+M3)
// 定位: 静态匹配部分 (运行时 gateway 调用); 动态状态(健康度/预判P99)由 Operator 附加
package decision

import (
	"fmt"
	"strconv"
	"strings"

	"github.com/fei232401/cyberrouter/internal/snapshot"
)

// Complexity 复杂度档位 (蓝图的 low/medium/high)
type Complexity string

const (
	Low    Complexity = "low"
	Medium Complexity = "medium"
	High   Complexity = "high"
)

// 复杂度评估阈值 (token 数): 可调, 当前为启发式初版
const (
	lowTokenBound    = 100
	mediumTokenBound = 1000
)

// Request 一次推理请求的特征 (gateway 运行时提取)
type Request struct {
	// TokenCount prompt token 数 (复杂度评估输入)
	TokenCount int
	// CacheHitProbability 0-1, 预测命中 LMCache 的概率 (cache-aware 联动点)
	CacheHitProbability float64
}

// Decision 决策输出 (可解释: Reasons 记录完整推理链, 供决策日志/审计)
type Decision struct {
	// Tier 选中的目标 tier (K8s Service 名)
	Tier string
	// FallbackChain 兜底链
	FallbackChain []string
	// RuleID 命中规则的 ID
	RuleID string
	// Reasons 决策推理链 (为什么选它, 候选为何被拒) — M3 可解释性
	Reasons []string
}

// EvaluateComplexity 复杂度评估: token 数分级 + 无关键词 (初版只用 token)
func EvaluateComplexity(tokenCount int) Complexity {
	switch {
	case tokenCount < lowTokenBound:
		return Low
	case tokenCount < mediumTokenBound:
		return Medium
	default:
		return High
	}
}

// MatchRule 按快照规则顺序 (优先级已降序) 匹配第一个命中的规则
func MatchRule(snap *snapshot.Snapshot, req Request) (*snapshot.Rule, []string) {
	var reasons []string
	complexity := EvaluateComplexity(req.TokenCount)
	reasons = append(reasons, fmt.Sprintf("复杂度评估: %d tokens → %s", req.TokenCount, complexity))

	for i := range snap.Rules {
		rule := &snap.Rules[i]
		matched, why := matchCondition(&rule.Condition, req, complexity)
		if matched {
			reasons = append(reasons, fmt.Sprintf("命中规则 %s (priority %d): %s", rule.ID, rule.Priority, why))
			return rule, reasons
		}
		reasons = append(reasons, fmt.Sprintf("规则 %s 未命中: %s", rule.ID, why))
	}
	reasons = append(reasons, "无规则命中 → 走默认路由")
	return nil, reasons
}

// SelectTier 决策入口: 匹配规则 → 返回带推理链的决策
func SelectTier(snap *snapshot.Snapshot, req Request) Decision {
	rule, reasons := MatchRule(snap, req)
	if rule == nil {
		return Decision{Reasons: reasons} // gateway 侧走默认路由
	}
	return Decision{
		Tier:         rule.Action.Tier,
		FallbackChain: rule.Action.FallbackChain,
		RuleID:       rule.ID,
		Reasons:      reasons,
	}
}

// matchCondition 判断单个规则的条件是否满足 (多个字段 = AND 语义)
func matchCondition(c *snapshot.Condition, req Request, complexity Complexity) (bool, string) {
	var parts []string
	// complexity 直接匹配
	if c.Complexity != "" {
		if string(complexity) != c.Complexity {
			return false, fmt.Sprintf("complexity %s ≠ %s", complexity, c.Complexity)
		}
		parts = append(parts, fmt.Sprintf("complexity=%s", c.Complexity))
	}
	// tokenCount 比较表达式, 如 "< 100"
	if c.TokenCount != "" {
		ok, err := compareInt(req.TokenCount, c.TokenCount)
		if err != nil {
			return false, fmt.Sprintf("tokenCount 表达式解析失败: %v", err)
		}
		if !ok {
			return false, fmt.Sprintf("tokenCount %d 不满足 %s", req.TokenCount, c.TokenCount)
		}
		parts = append(parts, fmt.Sprintf("tokenCount=%d %s", req.TokenCount, c.TokenCount))
	}
	// cacheHitProbability 比较表达式, 如 "> 0.5"
	if c.CacheHitProbability != "" {
		ok, err := compareFloat(req.CacheHitProbability, c.CacheHitProbability)
		if err != nil {
			return false, fmt.Sprintf("cacheHitProbability 表达式解析失败: %v", err)
		}
		if !ok {
			return false, fmt.Sprintf("cacheHitProbability %.2f 不满足 %s", req.CacheHitProbability, c.CacheHitProbability)
		}
		parts = append(parts, fmt.Sprintf("cacheHitProbability=%.2f %s", req.CacheHitProbability, c.CacheHitProbability))
	}
	return true, strings.Join(parts, " AND ")
}

// compareInt 解析 "op value" 比较整数 (支持 < > <= >= =)
func compareInt(v int, expr string) (bool, error) {
	op, valStr, err := parseExpr(expr)
	if err != nil {
		return false, err
	}
	val, err := strconv.Atoi(strings.TrimSpace(valStr))
	if err != nil {
		return false, fmt.Errorf("非整数: %q", valStr)
	}
	switch op {
	case "<":
		return v < val, nil
	case ">":
		return v > val, nil
	case "<=":
		return v <= val, nil
	case ">=":
		return v >= val, nil
	case "=":
		return v == val, nil
	default:
		return false, fmt.Errorf("不支持的运算符: %q", op)
	}
}

// compareFloat 解析 "op value" 比较浮点数 (命中率等)
func compareFloat(v float64, expr string) (bool, error) {
	op, valStr, err := parseExpr(expr)
	if err != nil {
		return false, err
	}
	val, err := strconv.ParseFloat(strings.TrimSpace(valStr), 64)
	if err != nil {
		return false, fmt.Errorf("非浮点数: %q", valStr)
	}
	switch op {
	case "<":
		return v < val, nil
	case ">":
		return v > val, nil
	case "<=":
		return v <= val, nil
	case ">=":
		return v >= val, nil
	case "=":
		return v == val, nil
	default:
		return false, fmt.Errorf("不支持的运算符: %q", op)
	}
}

// parseExpr 拆分 "op value" → (op, value)
func parseExpr(expr string) (string, string, error) {
	expr = strings.TrimSpace(expr)
	for _, op := range []string{"<=", ">=", "<", ">", "="} {
		if strings.HasPrefix(expr, op) {
			return op, strings.TrimSpace(expr[len(op):]), nil
		}
	}
	return "", "", fmt.Errorf("无法解析表达式: %q", expr)
}

// ============================================================
// 预判 P99 + 候选过滤 (蓝图 §3.3, "最难讲清的部分" 的简化版实现)
// 完整版: Little's law / M/G/1. 第一版: 当前P99 + 队列深度×边际延迟
// ============================================================

// TierHealth 某 tier 的动态状态 (Operator 从 Prometheus 采集后填充, gateway 决策时用)
type TierHealth struct {
	// Name tier 名 (K8s Service 名)
	Name string
	// Healthy 是否健康 (健康度 < 阈值剔除, 蓝图 §3.3.2b)
	Healthy bool
	// CurrentP99Ms 当前 P99 延迟 (毫秒)
	CurrentP99Ms float64
	// QueueDepth 当前队列深度 (max_num_seqs 排队的请求数)
	QueueDepth int
	// MarginalMs 每请求边际延迟 (毫秒, 简化排队模型)
	MarginalMs float64
}

// PredictP99 简化排队模型预测: 当前P99 + 队列深度 × 每请求边际延迟
func PredictP99(h TierHealth) float64 {
	return h.CurrentP99Ms + float64(h.QueueDepth)*h.MarginalMs
}

// FilterCandidates 候选过滤: 剔除不健康 / 预判 P99 超 SLO 的 tier
// 返回合格候选 + 每个被拒 tier 的理由 (决策日志)
func FilterCandidates(tiers []TierHealth, sloMs int) ([]TierHealth, []string) {
	var ok []TierHealth
	var rejected []string
	for _, t := range tiers {
		if !t.Healthy {
			rejected = append(rejected, fmt.Sprintf("%s: 不健康", t.Name))
			continue
		}
		predicted := PredictP99(t)
		if predicted > float64(sloMs) {
			rejected = append(rejected, fmt.Sprintf("%s: 预判P99 %.0fms > SLO %dms (队列 %d×%.0fms)", t.Name, predicted, sloMs, t.QueueDepth, t.MarginalMs))
			continue
		}
		ok = append(ok, t)
	}
	return ok, rejected
}
