// Package metrics — 从推理服务采集实时指标 (Phase 3, M4 集群状态感知)
// 数据源: vLLM /metrics 端点 (集群内 Service DNS 可达)
// 输出: TierStatus (GPU KV 水位 / LMCache 命中率 / 队列深度 / P99 预测)
package metrics

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/fei232401/cyberrouter/internal/decision"
	"github.com/fei232401/cyberrouter/internal/snapshot"
)

// 需要解析的 vLLM 指标 (Prometheus text format: `name{labels} value`)
var (
	reGpuUsage  = regexp.MustCompile(`(?m)^vllm:gpu_cache_usage_perc\{[^}]*\} ([\d.]+)`)
	reQueue     = regexp.MustCompile(`(?m)^vllm:num_requests_waiting\{[^}]*\} ([\d.]+)`)
	reHitQueries = regexp.MustCompile(`(?m)^vllm:prefix_cache_hits_total\{[^}]*\} ([\d.]+)`)
	reAllQueries = regexp.MustCompile(`(?m)^vllm:prefix_cache_queries_total\{[^}]*\} ([\d.]+)`)
)

// Client 从 vLLM metrics 端点拉取实时指标
type Client struct {
	// MetricsURL vLLM /metrics 端点 (集群内 Service DNS)
	MetricsURL string
	// HTTP client (超时控制)
	http *http.Client
}

// NewClient 构造采集器
func NewClient(metricsURL string) *Client {
	return &Client{
		MetricsURL: metricsURL,
		http:       &http.Client{Timeout: 5 * time.Second},
	}
}

// Fetch 拉取并解析 vLLM 指标, 返回该 tier 的动态状态
func (c *Client) Fetch(ctx context.Context) (*snapshot.TierStatus, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.MetricsURL, nil)
	if err != nil {
		return nil, err
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return nil, fmt.Errorf("拉取 %s 失败: %w", c.MetricsURL, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("metrics 端点 HTTP %d", resp.StatusCode)
	}
	body, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if err != nil {
		return nil, err
	}
	return parseVLLMMetrics(string(body))
}

// parseVLLMMetrics 解析 vLLM Prometheus 指标文本 → TierStatus
func parseVLLMMetrics(text string) (*snapshot.TierStatus, error) {
	st := &snapshot.TierStatus{Healthy: true}

	if m := reGpuUsage.FindStringSubmatch(text); m != nil {
		if v, err := strconv.ParseFloat(m[1], 64); err == nil {
			st.GpuUsagePct = v
		}
	}
	if m := reQueue.FindStringSubmatch(text); m != nil {
		if v, err := strconv.ParseFloat(m[1], 64); err == nil {
			st.QueueDepth = int(v)
		}
	}
	hits := parseFloatFirst(reHitQueries, text)
	queries := parseFloatFirst(reAllQueries, text)
	if queries > 0 {
		st.CacheHitRatio = hits / queries
	}
	return st, nil
}

func parseFloatFirst(re *regexp.Regexp, text string) float64 {
	if m := re.FindStringSubmatch(text); m != nil {
		if v, err := strconv.ParseFloat(m[1], 64); err == nil {
			return v
		}
	}
	return 0
}

// PredictP99 用简化排队模型预测 P99 (复用 decision 包的 TierHealth 逻辑)
// 注意: Go 不允许在外部包类型上定义方法, 故为包级函数
func PredictP99(st *snapshot.TierStatus, marginalMs float64) {
	st.MarginalMs = marginalMs
	// 基础 P99: 经验值 300ms + 队列深度 × 每请求边际延迟
	baseMs := 300.0
	st.PredictedP99Ms = decision.PredictP99(decision.TierHealth{
		CurrentP99Ms: baseMs,
		QueueDepth:   st.QueueDepth,
		MarginalMs:   marginalMs,
	})
}

// IsOverloaded 判断 GPU 是否高负载 (cache-aware / 预判降级用)
func IsOverloaded(st *snapshot.TierStatus, threshold float64) bool {
	return st.GpuUsagePct > threshold
}

// Stringify 便于日志
func Stringify(st *snapshot.TierStatus) string {
	var parts []string
	if st.GpuUsagePct > 0 {
		parts = append(parts, fmt.Sprintf("gpu=%.0f%%", st.GpuUsagePct*100))
	}
	if st.CacheHitRatio > 0 {
		parts = append(parts, fmt.Sprintf("hit=%.0f%%", st.CacheHitRatio*100))
	}
	if st.QueueDepth > 0 {
		parts = append(parts, fmt.Sprintf("queue=%d", st.QueueDepth))
	}
	if st.PredictedP99Ms > 0 {
		parts = append(parts, fmt.Sprintf("P99≈%.0fms", st.PredictedP99Ms))
	}
	if !st.Healthy {
		parts = append(parts, "UNHEALTHY")
	}
	if len(parts) == 0 {
		return "idle"
	}
	return strings.Join(parts, " ")
}
