// Package snapshot — 路由快照编译
// 职责: 把 RoutingPolicy CR (期望状态) 编译成数据面 gateway 可消费的 JSON 快照
// 设计: gateway 不 watch CRD, 只读 ConfigMap 快照 (解耦控制面/数据面, Envoy+xDS 模式)
package snapshot

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"sort"
)

// Snapshot 是数据面消费的路由快照 (JSON 结构, gateway 直接解析执行)
type Snapshot struct {
	Version           string `json:"version"`
	PolicyName        string `json:"policyName"`
	RolloutPercentage int    `json:"rolloutPercentage"`
	// Rules 按 Priority 降序排列 (gateway 顺序匹配, 先匹配先生效)
	Rules []Rule `json:"rules"`
	SLO   SLO    `json:"slo"`
	// Generation 来源 CR 的 metadata.generation (调试/审计)
	Generation int64 `json:"generation"`
}

// Rule 编译后的规则: 条件字段已标准化, gateway 无需解析 CR 表达式
type Rule struct {
	ID        string    `json:"id"`
	Priority  int       `json:"priority"`
	Condition Condition `json:"condition"`
	Action    Action    `json:"action"`
}

// Condition 匹配条件 (字符串比较表达式, 与 CR 一致)
type Condition struct {
	TokenCount          string `json:"tokenCount,omitempty"`
	Complexity          string `json:"complexity,omitempty"`
	CacheHitProbability string `json:"cacheHitProbability,omitempty"`
}

// Action 路由动作
type Action struct {
	Tier          string   `json:"tier"`
	FallbackChain []string `json:"fallbackChain,omitempty"`
}

// SLO 各复杂度档位 P99 预算 (毫秒)
type SLO struct {
	Low    int `json:"low"`
	Medium int `json:"medium"`
	High   int `json:"high"`
}

// Compile 从路由规则编译快照 (controller 负责把 CR 字段转换为 []Rule)
func Compile(policyName string, rolloutPercentage *int, rules []Rule, slo SLO, generation int64) (*Snapshot, error) {
	snap := &Snapshot{
		Version:    "1",
		PolicyName: policyName,
		SLO:        slo,
		Generation: generation,
	}
	if rolloutPercentage != nil {
		snap.RolloutPercentage = *rolloutPercentage
	} else {
		snap.RolloutPercentage = 100 // 默认全量
	}
	snap.Rules = make([]Rule, len(rules))
	copy(snap.Rules, rules)
	// 按 Priority 降序排列 (高优先级在前, gateway 顺序匹配)
	sort.SliceStable(snap.Rules, func(i, j int) bool {
		return snap.Rules[i].Priority > snap.Rules[j].Priority
	})
	return snap, nil
}

// Hash 计算快照内容的稳定哈希 (gateway 增量同步 + 决策日志审计)
func (s *Snapshot) Hash() string {
	data, err := json.Marshal(s)
	if err != nil {
		return "ERR"
	}
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:8])
}

// MarshalJSON 序列化 (供写 ConfigMap)
func (s *Snapshot) MarshalJSON() ([]byte, error) {
	type alias Snapshot
	return json.Marshal((*alias)(s))
}

// Validate 检查快照合法性 (gateway 侧复用)
func (s *Snapshot) Validate() error {
	if s.PolicyName == "" {
		return fmt.Errorf("policyName 为空")
	}
	for _, r := range s.Rules {
		if r.Action.Tier == "" {
			return fmt.Errorf("rule %s: action.tier 为空", r.ID)
		}
	}
	return nil
}
