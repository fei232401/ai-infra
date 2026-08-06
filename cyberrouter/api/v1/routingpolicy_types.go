package v1

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// ============================================================
// RoutingPolicy CRD — CyberRouter 控制面核心对象
// 设计来源: BLUEPRINT §3.2
// 策略即代码: CRD 化获得版本化 + GitOps + 审计, 灰度发布 (rollout.percentage)
// ============================================================

// RoutingPolicySpec 定义 RoutingPolicy 的期望状态
type RoutingPolicySpec struct {
	// Rollout 控制本策略版本的灰度比例 (0-100)
	// +optional
	Rollout RolloutSpec `json:"rollout,omitempty"`

	// Rules 按 priority 降序评估, 数字大者先匹配; 全不匹配走默认规则
	Rules []Rule `json:"rules"`

	// SLO 每个复杂度档位的 P99 延迟预算 (毫秒)
	// +optional
	SLO SLOSpec `json:"slo,omitempty"`
}

// RolloutSpec 灰度发布控制
type RolloutSpec struct {
	// Percentage 灰度比例, 0-100
	// +optional
	Percentage *int `json:"percentage,omitempty"`

	// TenantFilter 租户过滤 (留作多租户扩展, 当前 null=全量)
	// +optional
	TenantFilter string `json:"tenantFilter,omitempty"`
}

// Rule 一条路由规则: 条件匹配 + 动作
type Rule struct {
	// ID 规则唯一标识 (决策日志用)
	ID string `json:"id"`

	// Priority 优先级, 数字大者先评估
	Priority int `json:"priority"`

	// Condition 请求匹配条件 (AND 语义: 多个字段同时满足)
	Condition ConditionSpec `json:"condition"`

	// Action 条件命中时的路由动作
	Action ActionSpec `json:"action"`
}

// ConditionSpec 请求匹配条件
// 用字符串承载比较表达式 (如 "< 100", "> 0.5"), 便于 CR 表达而无需代码改动
type ConditionSpec struct {
	// TokenCount token 数比较, 如 "< 100"
	// +optional
	TokenCount string `json:"tokenCount,omitempty"`

	// Complexity 复杂度档位: low/medium/high
	// +optional
	Complexity string `json:"complexity,omitempty"`

	// CacheHitProbability 命中率比较, 如 "> 0.5" (cache-aware 联动点)
	// +optional
	CacheHitProbability string `json:"cacheHitProbability,omitempty"`
}

// ActionSpec 路由动作
type ActionSpec struct {
	// Tier 目标 tier, 值 = K8s Service 名 (如 vllm-3b-service / ollama-service)
	Tier string `json:"tier"`

	// FallbackChain 兜底链, 有序降级列表
	// +optional
	FallbackChain []string `json:"fallbackChain,omitempty"`
}

// SLOSpec 各复杂度档位的延迟预算
type SLOSpec struct {
	// +optional
	Low SLOTier `json:"low,omitempty"`
	// +optional
	Medium SLOTier `json:"medium,omitempty"`
	// +optional
	High SLOTier `json:"high,omitempty"`
}

// SLOTier 单个档位的 P99 预算
type SLOTier struct {
	// MaxP99Ms P99 延迟预算 (毫秒)
	MaxP99Ms int `json:"maxP99Ms"`
}

// RoutingPolicyStatus 定义观察到的状态 (Operator 回写)
type RoutingPolicyStatus struct {
	// ObservedGeneration 最近一次 reconcile 的 generation
	// +optional
	ObservedGeneration int64 `json:"observedGeneration,omitempty"`

	// SnapshotHash 编译后路由快照的哈希 (gateway 同步校验用)
	// +optional
	SnapshotHash string `json:"snapshotHash,omitempty"`

	// Conditions reconcile 状态条件
	// +optional
	Conditions []metav1.Condition `json:"conditions,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:resource:shortName=rp

// RoutingPolicy 是路由策略 API 的模式 (Schema)
type RoutingPolicy struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   RoutingPolicySpec   `json:"spec,omitempty"`
	Status RoutingPolicyStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true

// RoutingPolicyList 包含一组 RoutingPolicy
type RoutingPolicyList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []RoutingPolicy `json:"items"`
}

func init() {
	SchemeBuilder.Register(&RoutingPolicy{}, &RoutingPolicyList{})
}
