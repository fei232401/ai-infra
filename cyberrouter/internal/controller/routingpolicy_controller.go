// Controller for RoutingPolicy — reconcile 循环的核心
// 职责: watch RoutingPolicy 变化 → 编译路由快照 → 写 ConfigMap (数据面 gateway 消费)
// 模式: "慢路径定策略" (控制面) vs gateway "快路径执行" (数据面), Envoy+xDS
package controller

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/go-logr/logr"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/log"

	routingv1 "github.com/fei232401/cyberrouter/api/v1"
	"github.com/fei232401/cyberrouter/internal/metrics"
	"github.com/fei232401/cyberrouter/internal/snapshot"
)

const (
	// SnapshotConfigMap 是数据面 gateway 消费的路由快照 (单一真源)
	SnapshotConfigMap = "cyberrouter-routing-snapshot"
	// SnapshotNamespace 与 gateway 部署同 namespace
	SnapshotNamespace = "ai-platform"
	// DynamicRefreshInterval 动态状态刷新周期 (M4 集群状态感知, 蓝图 §3.5: 5s)
	DynamicRefreshInterval = 5 * time.Second
)

// TierEndpoint 一个待监控的 tier (名称 + 指标端点 + 排队模型参数)
type TierEndpoint struct {
	// Name tier 名 (与规则 action.tier 对应)
	Name string
	// MetricsURL Prometheus/metrics 端点 (集群内 Service DNS)
	MetricsURL string
	// MarginalMs 每请求边际延迟 (简化排队模型, 预判 P99)
	MarginalMs float64
}

// RoutingPolicyReconciler 把 RoutingPolicy 的期望状态编译成数据面可消费的快照
type RoutingPolicyReconciler struct {
	client.Client
	Scheme *runtime.Scheme
	// Metrics 指标采集器 (拉各 tier 动态状态)
	Metrics *metrics.Client
	// TierEndpoints 需要监控动态状态的 tier 列表
	TierEndpoints []TierEndpoint
}

// Reconcile 核心循环: 每次 RoutingPolicy 变化 (增/改/删) 都被 controller-runtime 调用
func (r *RoutingPolicyReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	logger := log.FromContext(ctx).WithValues("routingpolicy", req.NamespacedName.String())

	// 1. 获取被 watch 的 RoutingPolicy 对象
	var rp routingv1.RoutingPolicy
	if err := r.Get(ctx, req.NamespacedName, &rp); err != nil {
		if apierrors.IsNotFound(err) {
			// 策略被删除: 清理快照 ConfigMap (数据面回到默认路由)
			logger.Info("策略删除, 清理快照")
			return ctrl.Result{}, r.deleteSnapshot(ctx)
		}
		return ctrl.Result{}, err
	}

	// 2. 采集各 tier 动态状态 (Phase 3: GPU 水位/命中率/队列 → 预判 P99)
	tiers := r.collectTierStatus(ctx, logger)

	// 3. 编译路由快照 (静态规则 + 动态 tiers)
	snap, err := compileSnapshot(&rp, tiers)
	if err != nil {
		logger.Error(err, "编译快照失败")
		return ctrl.Result{}, err
	}
	hash := snap.Hash()

	// 4. hash 未变 → 跳过同步 (但动态状态可能后续变化, 定期 requeue 继续监控)
	if rp.Status.SnapshotHash == hash {
		logger.V(1).Info("快照无变化, 跳过同步", "hash", hash)
		return ctrl.Result{RequeueAfter: DynamicRefreshInterval}, nil
	}

	// 5. 写快照到 ConfigMap (数据面 gateway 消费)
	if err := r.syncSnapshot(ctx, snap); err != nil {
		return ctrl.Result{}, fmt.Errorf("同步快照到 ConfigMap 失败: %w", err)
	}

	// 6. 回写 Status (ObservedGeneration + 真实 hash)
	if err := r.updateStatus(ctx, &rp, hash); err != nil {
		return ctrl.Result{}, fmt.Errorf("更新 status 失败: %w", err)
	}

	logger.Info("快照已同步",
		"hash", hash,
		"rules", len(snap.Rules),
		"tiers", len(snap.Tiers),
		"generation", rp.Generation,
		"gpu", tierSummary(snap.Tiers),
	)
	// 定期刷新动态状态 (慢路径定策略, 5s 级)
	return ctrl.Result{RequeueAfter: DynamicRefreshInterval}, nil
}

// collectTierStatus 采集各 tier 动态状态 (metrics 不可达 → 标记不健康)
func (r *RoutingPolicyReconciler) collectTierStatus(ctx context.Context, logger logr.Logger) []snapshot.TierStatus {
	var tiers []snapshot.TierStatus
	if r.Metrics == nil {
		return tiers
	}
	for _, ep := range r.TierEndpoints {
		st, err := r.Metrics.Fetch(ctx)
		if err != nil {
			logger.Info("tier 指标拉取失败", "tier", ep.Name, "err", err.Error())
			tiers = append(tiers, snapshot.TierStatus{Name: ep.Name, Healthy: false})
			continue
		}
		st.Name = ep.Name
		metrics.PredictP99(st, ep.MarginalMs)
		tiers = append(tiers, *st)
	}
	return tiers
}

// tierSummary 摘要 tier 动态状态 (日志/决策审计)
func tierSummary(tiers []snapshot.TierStatus) string {
	var parts []string
	for _, t := range tiers {
		p := fmt.Sprintf("%s{%s}", t.Name, strings.Join(statusFields(t), " "))
		parts = append(parts, p)
	}
	return strings.Join(parts, " ")
}

func statusFields(t snapshot.TierStatus) []string {
	var f []string
	if t.GpuUsagePct > 0 {
		f = append(f, fmt.Sprintf("gpu=%.0f%%", t.GpuUsagePct*100))
	}
	if t.CacheHitRatio > 0 {
		f = append(f, fmt.Sprintf("hit=%.0f%%", t.CacheHitRatio*100))
	}
	if t.QueueDepth > 0 {
		f = append(f, fmt.Sprintf("queue=%d", t.QueueDepth))
	}
	if t.PredictedP99Ms > 0 {
		f = append(f, fmt.Sprintf("P99≈%.0fms", t.PredictedP99Ms))
	}
	if !t.Healthy {
		f = append(f, "UNHEALTHY")
	}
	return f
}

// compileSnapshot 把 CR 字段转换为快照 (解耦: CR 表达"用户意图", 快照表达"数据面执行")
func compileSnapshot(rp *routingv1.RoutingPolicy, tiers []snapshot.TierStatus) (*snapshot.Snapshot, error) {
	rules := make([]snapshot.Rule, len(rp.Spec.Rules))
	for i, r := range rp.Spec.Rules {
		rules[i] = snapshot.Rule{
			ID:       r.ID,
			Priority: r.Priority,
			Condition: snapshot.Condition{
				TokenCount:          r.Condition.TokenCount,
				Complexity:          r.Condition.Complexity,
				CacheHitProbability: r.Condition.CacheHitProbability,
			},
			Action: snapshot.Action{
				Tier:          r.Action.Tier,
				FallbackChain: r.Action.FallbackChain,
			},
		}
	}
	slo := snapshot.SLO{
		Low:    rp.Spec.SLO.Low.MaxP99Ms,
		Medium: rp.Spec.SLO.Medium.MaxP99Ms,
		High:   rp.Spec.SLO.High.MaxP99Ms,
	}
	snap, err := snapshot.Compile(rp.Name, rp.Spec.Rollout.Percentage, rules, slo, rp.Generation)
	if err != nil {
		return nil, err
	}
	snap.Tiers = tiers // Phase 3: 附加动态状态 (gateway 决策时读)
	return snap, nil
}

// syncSnapshot 幂等地写快照 ConfigMap (存在则更新, 不存在则创建)
func (r *RoutingPolicyReconciler) syncSnapshot(ctx context.Context, snap *snapshot.Snapshot) error {
	data, err := json.MarshalIndent(snap, "", "  ")
	if err != nil {
		return err
	}
	cm := &corev1.ConfigMap{
		ObjectMeta: metav1.ObjectMeta{
			Name:      SnapshotConfigMap,
			Namespace: SnapshotNamespace,
			Labels:    map[string]string{"app.kubernetes.io/part-of": "cyberrouter"},
		},
		Data: map[string]string{"snapshot.json": string(data)},
	}

	var existing corev1.ConfigMap
	if err := r.Get(ctx, client.ObjectKey{Name: SnapshotConfigMap, Namespace: SnapshotNamespace}, &existing); err != nil {
		if apierrors.IsNotFound(err) {
			return r.Create(ctx, cm)
		}
		return err
	}
	existing.Data = cm.Data
	return r.Update(ctx, &existing)
}

// deleteSnapshot 删除快照 ConfigMap (策略删除时)
func (r *RoutingPolicyReconciler) deleteSnapshot(ctx context.Context) error {
	var existing corev1.ConfigMap
	if err := r.Get(ctx, client.ObjectKey{Name: SnapshotConfigMap, Namespace: SnapshotNamespace}, &existing); err != nil {
		if apierrors.IsNotFound(err) {
			return nil
		}
		return err
	}
	return r.Delete(ctx, &existing)
}

// updateStatus 回写观察状态 (ObservedGeneration + SnapshotHash)
func (r *RoutingPolicyReconciler) updateStatus(ctx context.Context, rp *routingv1.RoutingPolicy, hash string) error {
	if rp.Status.ObservedGeneration == rp.Generation && rp.Status.SnapshotHash == hash {
		return nil // 无变化, 避免无效更新
	}
	patch := rp.DeepCopy()
	patch.Status.ObservedGeneration = rp.Generation
	patch.Status.SnapshotHash = hash
	return r.Status().Patch(ctx, patch, client.MergeFrom(rp))
}

// SetupWithManager 注册 controller, 声明 watch 哪些对象
func (r *RoutingPolicyReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&routingv1.RoutingPolicy{}).
		Named("routingpolicy").
		Complete(r)
}
