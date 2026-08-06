// Controller for RoutingPolicy — reconcile 循环的核心
// 职责: watch RoutingPolicy 变化 → 编译路由快照 → 写 ConfigMap (数据面 gateway 消费)
// 模式: "慢路径定策略" (控制面) vs gateway "快路径执行" (数据面)
package controller

import (
	"context"
	"fmt"

	"k8s.io/apimachinery/pkg/runtime"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/log"

	routingv1 "github.com/fei232401/cyberrouter/api/v1"
)

// RoutingPolicyReconciler 把 RoutingPolicy 的期望状态编译成数据面可消费的快照
type RoutingPolicyReconciler struct {
	client.Client
	Scheme *runtime.Scheme
}

// Reconcile 是核心循环: 每次 RoutingPolicy 变化 (增/改/删) 都被 controller-runtime 调用
// controller-runtime 框架保证: 对象变化 → 触发此函数 → 返回结果决定是否重试
func (r *RoutingPolicyReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	logger := log.FromContext(ctx).WithValues("routingpolicy", req.NamespacedName.String())

	// 1. 获取被 watch 的 RoutingPolicy 对象
	var rp routingv1.RoutingPolicy
	if err := r.Get(ctx, req.NamespacedName, &rp); err != nil {
		// 资源已删除: 需要清理快照, 但 IgnoreNotFound 表示"删除后的正常路径"
		return ctrl.Result{}, client.IgnoreNotFound(err)
	}

	// 2. 编译路由快照 (当前为骨架: 打印信息; 后续接 decision 决策引擎)
	//    TODO(P2-3): decision.Compile(&rp.Spec) → snapshot
	logger.Info("策略变更触发 reconcile",
		"rules", len(rp.Spec.Rules),
		"generation", rp.Generation,
		"rollout_percentage", rp.Spec.Rollout.Percentage,
		"prev_snapshot_hash", rp.Status.SnapshotHash,
	)

	// 3. (TODO P2-2) 写快照到 ConfigMap + 更新 Status.SnapshotHash
	//    这里先占位, 保证 reconcile 闭环能跑
	if err := r.updateStatus(ctx, &rp, "0000000000000000"); err != nil {
		return ctrl.Result{}, fmt.Errorf("更新 status 失败: %w", err)
	}

	return ctrl.Result{}, nil
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

// SetupWithManager 把 controller 注册进 manager, 声明 watch 哪些对象
func (r *RoutingPolicyReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&routingv1.RoutingPolicy{}).
		Named("routingpolicy").
		Complete(r)
}
