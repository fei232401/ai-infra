package decision

import (
	"testing"

	"github.com/fei232401/cyberrouter/internal/snapshot"
)

// 构造 cost-first-default 快照 (与 config/samples 一致)
func testSnapshot() *snapshot.Snapshot {
	rules := []snapshot.Rule{
		{ID: "rule-001", Priority: 100,
			Condition: snapshot.Condition{TokenCount: "< 100", CacheHitProbability: "> 0.5"},
			Action:    snapshot.Action{Tier: "ollama-service", FallbackChain: []string{"ollama-service", "vllm-3b-service"}}},
		{ID: "rule-002", Priority: 90,
			Condition: snapshot.Condition{Complexity: "high"},
			Action:    snapshot.Action{Tier: "vllm-3b-service", FallbackChain: []string{"vllm-3b-service", "ollama-service"}}},
	}
	s, _ := snapshot.Compile("cost-first-default", nil, rules, snapshot.SLO{Low: 200, Medium: 1000, High: 5000}, 1)
	return s
}

func TestEvaluateComplexity(t *testing.T) {
	cases := []struct {
		tokens int
		want   Complexity
	}{
		{50, Low},
		{500, Medium},
		{5000, High},
	}
	for _, c := range cases {
		got := EvaluateComplexity(c.tokens)
		if got != c.want {
			t.Errorf("EvaluateComplexity(%d) = %s, want %s", c.tokens, got, c.want)
		}
	}
}

func TestSelectTier_ShortAndCacheHit(t *testing.T) {
	snap := testSnapshot()
	// 短请求 + 高命中概率 → rule-001 → CPU tier
	req := Request{TokenCount: 50, CacheHitProbability: 0.8}
	d := SelectTier(snap, req)
	if d.Tier != "ollama-service" {
		t.Errorf("Tier = %s, want ollama-service (推理: %v)", d.Tier, d.Reasons)
	}
	if d.RuleID != "rule-001" {
		t.Errorf("RuleID = %s, want rule-001", d.RuleID)
	}
}

func TestSelectTier_HighComplexity(t *testing.T) {
	snap := testSnapshot()
	// 长请求 (高复杂度) → rule-002 → GPU tier
	req := Request{TokenCount: 5000, CacheHitProbability: 0.0}
	d := SelectTier(snap, req)
	if d.Tier != "vllm-3b-service" {
		t.Errorf("Tier = %s, want vllm-3b-service (推理: %v)", d.Tier, d.Reasons)
	}
	if d.RuleID != "rule-002" {
		t.Errorf("RuleID = %s, want rule-002", d.RuleID)
	}
}

func TestSelectTier_ShortButNoCacheHit(t *testing.T) {
	snap := testSnapshot()
	// 短请求但命中率低 → rule-001 的 cacheHitProbability 不满足 → 落到 rule-002? 也不满足 (低复杂度) → 默认
	req := Request{TokenCount: 50, CacheHitProbability: 0.1}
	d := SelectTier(snap, req)
	if d.Tier != "" {
		t.Errorf("Tier = %s, want 空 (默认路由), 推理: %v", d.Tier, d.Reasons)
	}
}

func TestDecisionHasReasons(t *testing.T) {
	snap := testSnapshot()
	req := Request{TokenCount: 5000}
	d := SelectTier(snap, req)
	if len(d.Reasons) == 0 {
		t.Error("决策应有推理链 (M3 可解释性)")
	}
	for _, r := range d.Reasons {
		t.Logf("  推理: %s", r) // 展示决策日志可读性
	}
}

func TestCompareInt_Expressions(t *testing.T) {
	cases := []struct {
		v    int
		expr string
		want bool
	}{
		{50, "< 100", true},
		{150, "< 100", false},
		{100, "<= 100", true},
		{100, "< 100", false},
		{150, "> 100", true},
	}
	for _, c := range cases {
		got, err := compareInt(c.v, c.expr)
		if err != nil {
			t.Fatalf("compareInt(%d, %q) error: %v", c.v, c.expr, err)
		}
		if got != c.want {
			t.Errorf("compareInt(%d, %q) = %v, want %v", c.v, c.expr, got, c.want)
		}
	}
}

func TestMatchRule_SortedByPriority(t *testing.T) {
	snap := testSnapshot()
	// 快照应已按 priority 降序 (rule-001 在前)
	if snap.Rules[0].ID != "rule-001" || snap.Rules[1].ID != "rule-002" {
		t.Errorf("快照规则未按优先级排序: %+v", snap.Rules)
	}
}

func TestPredictP99_QueueModel(t *testing.T) {
	// 当前 P99 300ms + 队列 5 × 边际 80ms = 700ms
	h := TierHealth{Name: "vllm-3b-service", Healthy: true, CurrentP99Ms: 300, QueueDepth: 5, MarginalMs: 80}
	got := PredictP99(h)
	if got != 700 {
		t.Errorf("PredictP99 = %.0f, want 700", got)
	}
}

func TestFilterCandidates_RejectsUnhealthyAndSLO(t *testing.T) {
	tiers := []TierHealth{
		{Name: "gpu", Healthy: true, CurrentP99Ms: 300, QueueDepth: 2, MarginalMs: 80},       // 300+160=460
		{Name: "cpu", Healthy: true, CurrentP99Ms: 150, QueueDepth: 1, MarginalMs: 40},       // 150+40=190
		{Name: "gpu-busy", Healthy: true, CurrentP99Ms: 900, QueueDepth: 10, MarginalMs: 80}, // 900+800=1700
		{Name: "dead", Healthy: false, CurrentP99Ms: 0, QueueDepth: 0, MarginalMs: 0},        // 不健康
	}
	// SLO 500ms: 只剩 gpu(460) 和 cpu(190)
	ok, rejected := FilterCandidates(tiers, 500)
	if len(ok) != 2 {
		t.Errorf("合格候选 = %d, want 2 (理由: %v)", len(ok), rejected)
	}
	if len(rejected) != 2 {
		t.Errorf("被拒候选 = %d, want 2 (gpu-busy 超SLO + dead 不健康): %v", len(rejected), rejected)
	}
	for _, r := range rejected {
		t.Logf("  拒绝理由: %s", r) // 决策日志可读性
	}
}

func TestCacheAwareP99_DiscountByHitRatio(t *testing.T) {
	h := TierHealth{Name: "vllm", Healthy: true, CurrentP99Ms: 100, QueueDepth: 2, MarginalMs: 50} // 原始 200
	raw := PredictP99(h)
	if raw != 200 {
		t.Fatalf("PredictP99 = %.0f, want 200", raw)
	}
	// 满命中 + boost 0.5 → 200×0.5 = 100
	h.CacheHitRatio = 1.0
	if got := CacheAwareP99(h, 0.5); got != 100 {
		t.Errorf("满命中 CacheAwareP99 = %.1f, want 100", got)
	}
	// 无命中 → 不变化
	h.CacheHitRatio = 0.0
	if got := CacheAwareP99(h, 0.5); got != 200 {
		t.Errorf("零命中 CacheAwareP99 = %.1f, want 200", got)
	}
	// boost=0 → 关闭打折
	h.CacheHitRatio = 1.0
	if got := CacheAwareP99(h, 0.0); got != 200 {
		t.Errorf("boost=0 CacheAwareP99 = %.1f, want 200", got)
	}
}

func TestCacheAwareCandidates_HighHitKeepsTier(t *testing.T) {
	tiers := []TierHealth{
		{Name: "vllm", Healthy: true, CurrentP99Ms: 300, QueueDepth: 2, MarginalMs: 50, CacheHitRatio: 0.95}, // 原始400, 打折400×0.525=210
		{Name: "ollama", Healthy: true, CurrentP99Ms: 100, QueueDepth: 1, MarginalMs: 20},                    // 120
	}
	// SLO 300: 原始版会拒 vllm (400>300), cache-aware 后 210 ≤ 300 → 保留
	ok, _ := CacheAwareCandidates(tiers, 300, 0.5)
	if len(ok) != 2 {
		t.Errorf("cache-aware 合格候选 = %d, want 2 (vllm 因高命中率被保留)", len(ok))
	}
	// boost=0 → 行为与 FilterCandidates 一致: 只留 ollama
	ok2, _ := CacheAwareCandidates(tiers, 300, 0.0)
	if len(ok2) != 1 {
		t.Errorf("boost=0 合格候选 = %d, want 1 (只留 ollama)", len(ok2))
	}
}
