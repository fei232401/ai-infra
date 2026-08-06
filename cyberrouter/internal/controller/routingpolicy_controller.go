// Controller for RoutingPolicy — reconcile 循环的核心
// 职责: watch RoutingPolicy 变化 → 编译路由快照 → 写 ConfigMap (数据面 gateway 消费)
// 模式: "慢路径定策略" (控制面) vs gateway "快路径执行" (数据面), Envoy+xDS
package controller

import (
	"context"
	"encoding/json"
	"fmt"

	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/log"

	routingv1 "github.com/fei232401/cyberrouter/api/v1"
	"github.com/fei232401/cyberrouter/internal/snapshot"
)

const (
	// SnapshotConfigMap 是数据面 gateway 消费的路由快照 (单一真源)
	SnapshotConfigMap = "cyberrouter-routing-snapshot"
	// SnapshotNamespace 与 gateway 部署同 namespace
	SnapshotNamespace = "ai-platform"
)

// RoutingPolicyReconciler 把 RoutingPolicy 的期望状态编译成数据面可消费的快照
type RoutingPolicyReconciler struct {
	client.Client
	Scheme *runtime.Scheme
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

	// 2. 编译路由快照 (CR → 数据面 JSON)
	snap, err := compileSnapshot(&rp)
	if err != nil {
		logger.Error(err, "编译快照失败")
		return ctrl.Result{}, err
	}
	hash := snap.Hash()

	// 3. 已同步且 hash 未变 → 跳过 (幂等, 避免无效写)
	if rp.Status.SnapshotHash == hash {
		logger.Info("快照无变化, 跳过同步", "hash", hash)
		return ctrl.Result{}, nil
	}

	// 4. 写快照到 ConfigMap (数据面 gateway watch 这个 ConfigMap)
	if err := r.syncSnapshot(ctx, snap); err != nil {
		return ctrl.Result{}, fmt.Errorf("同步快照到 ConfigMap 失败: %w", err)
	}

	// 5. 回写 Status (ObservedGeneration + 真实 hash)
	if err := r.updateStatus(ctx, &rp, hash); err != nil {
		return ctrl.Result{}, fmt.Errorf("更新 status 失败: %w", err)
	}

	logger.Info("快照已同步",
		"hash", hash,
		"rules", len(snap.Rules),
		"generation", rp.Generation,
	)
	return ctrl.Result{}, nil
}

// compileSnapshot 把 CR 字段转换为快照 (解耦: CR 表达"用户意图", 快照表达"数据面执行")
func compileSnapshot(rp *routingv1.RoutingPolicy) (*snapshot.Snapshot, error) {
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
	return snapshot.Compile(rp.Name, rp.Spec.Rollout.Percentage, rules, slo, rp.Generation)
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
