// CyberRouter v2 — K8s Operator 调度器 (控制面)
// 职责: watch RoutingPolicy CRD + 集群/缓存状态 → 编译路由快照 → 同步数据面 gateway
// 架构来源: BLUEPRINT §3, Envoy+xDS 模式的"慢路径定策略"
package main

import (
	"flag"
	"os"

	"k8s.io/apimachinery/pkg/runtime"
	utilruntime "k8s.io/apimachinery/pkg/util/runtime"
	clientgoscheme "k8s.io/client-go/kubernetes/scheme"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/healthz"
	"sigs.k8s.io/controller-runtime/pkg/log/zap"
	metricsserver "sigs.k8s.io/controller-runtime/pkg/metrics/server"

	routingv1 "github.com/fei232401/cyberrouter/api/v1"
	"github.com/fei232401/cyberrouter/internal/controller"
)

var (
	scheme   = runtime.NewScheme()
	setupLog = ctrl.Log.WithName("setup")
)

func init() {
	utilruntime.Must(clientgoscheme.AddToScheme(scheme))
	utilruntime.Must(routingv1.AddToScheme(scheme))
}

func main() {
	var metricsAddr string
	var probeAddr string
	flag.StringVar(&metricsAddr, "metrics-bind-address", ":8080", "metrics 监听地址")
	flag.StringVar(&probeAddr, "health-probe-bind-address", ":8081", "health probe 监听地址")
	opts := zap.Options{Development: true}
	opts.BindFlags(flag.CommandLine)
	flag.Parse()

	ctrl.SetLogger(zap.New(zap.UseFlagOptions(&opts)))

	mgr, err := ctrl.NewManager(ctrl.GetConfigOrDie(), ctrl.Options{
		Scheme:                 scheme,
		Metrics:                metricsserver.Options{BindAddress: metricsAddr},
		HealthProbeBindAddress: probeAddr,
		LeaderElection:         false, // 单副本本地环境, 不需要 leader election
	})
	if err != nil {
		setupLog.Error(err, "无法启动 manager")
		os.Exit(1)
	}

	if err := (&controller.RoutingPolicyReconciler{
		Client: mgr.GetClient(),
		Scheme: mgr.GetScheme(),
	}).SetupWithManager(mgr); err != nil {
		setupLog.Error(err, "无法创建 controller", "controller", "RoutingPolicy")
		os.Exit(1)
	}

	if err := mgr.AddHealthzCheck("healthz", healthz.Ping); err != nil {
		setupLog.Error(err, "无法设置 health check")
		os.Exit(1)
	}
	if err := mgr.AddReadyzCheck("readyz", healthz.Ping); err != nil {
		setupLog.Error(err, "无法设置 ready check")
		os.Exit(1)
	}

	setupLog.Info("CyberRouter Operator 启动")
	if err := mgr.Start(ctrl.SetupSignalHandler()); err != nil {
		setupLog.Error(err, "manager 运行失败")
		os.Exit(1)
	}
}
