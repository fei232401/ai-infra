package metrics

import (
	"testing"

	"github.com/fei232401/cyberrouter/internal/snapshot"
)

func TestParseVLLMMetrics(t *testing.T) {
	// 样例 vLLM Prometheus 指标文本 (与集群实测格式一致)
	text := `# HELP vllm:gpu_cache_usage_perc KV-cache usage. 1 means 100 percent usage.
# TYPE vllm:gpu_cache_usage_perc gauge
vllm:gpu_cache_usage_perc{engine="0",model_name="/models/qwen2.5-3b-awq"} 0.65
vllm:num_requests_running{engine="0",model_name="/models/qwen2.5-3b-awq"} 10
vllm:num_requests_waiting{engine="0",model_name="/models/qwen2.5-3b-awq"} 3
vllm:prefix_cache_hits_total{engine="0",model_name="/models/qwen2.5-3b-awq"} 65024
vllm:prefix_cache_queries_total{engine="0",model_name="/models/qwen2.5-3b-awq"} 235431
`
	st, err := parseVLLMMetrics(text)
	if err != nil {
		t.Fatalf("parse error: %v", err)
	}
	if !st.Healthy {
		t.Error("Healthy 应为 true")
	}
	if st.GpuUsagePct != 0.65 {
		t.Errorf("GpuUsagePct = %v, want 0.65", st.GpuUsagePct)
	}
	if st.Running != 10 {
		t.Errorf("Running = %d, want 10", st.Running)
	}
	if st.QueueDepth != 3 {
		t.Errorf("QueueDepth = %d, want 3", st.QueueDepth)
	}
	// 命中率 = 65024/235431 ≈ 0.276
	wantHit := 65024.0 / 235431.0
	if diff := abs(st.CacheHitRatio - wantHit); diff > 0.001 {
		t.Errorf("CacheHitRatio = %v, want ~%v", st.CacheHitRatio, wantHit)
	}
}

func TestPredictP99_RunningModel(t *testing.T) {
	// 5 个并发 × 边际 80ms + 基础 300ms = 700ms
	st := &snapshot.TierStatus{Running: 5}
	PredictP99(st, 80)
	if st.PredictedP99Ms != 700 {
		t.Errorf("PredictedP99Ms = %v, want 700", st.PredictedP99Ms)
	}
}

func TestIsOverloaded(t *testing.T) {
	st := &snapshot.TierStatus{GpuUsagePct: 0.92}
	if !IsOverloaded(st, 0.85) {
		t.Error("GPU 92% 应判定为高负载 (阈值 85%)")
	}
	st2 := &snapshot.TierStatus{GpuUsagePct: 0.4}
	if IsOverloaded(st2, 0.85) {
		t.Error("GPU 40% 不应判定为高负载")
	}
}

func TestStringify(t *testing.T) {
	st := &snapshot.TierStatus{GpuUsagePct: 0.65, CacheHitRatio: 0.5, QueueDepth: 3}
	s := Stringify(st)
	if len(s) == 0 {
		t.Error("Stringify 不应为空")
	}
	t.Logf("摘要: %s", s)
}

func abs(x float64) float64 {
	if x < 0 {
		return -x
	}
	return x
}
