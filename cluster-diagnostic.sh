#!/bin/bash
###############################################################################
# 赛博图书馆 · 集群全面诊断脚本
# 用途：1) 验证监控系统功能  2) 交接时快速同步集群状态
# 保存到：~/projects/project/cluster-diagnostic.sh
# 用法：bash ~/projects/project/cluster-diagnostic.sh 2>&1 | tee ~/projects/project/diagnostic-$(date +%Y%m%d-%H%M%S).log
###############################################################################

set -uo pipefail
# ========== 新增：内置日志分流 ==========
LOG_FILE="./diagnostic-$(date +%Y%m%d-%H%M%S).log"
# 同时打印终端 + 写入日志，stdout/stderr全部捕获
exec > >(tee "${LOG_FILE}") 2>&1
# ========================================
export KUBECONFIG=${KUBECONFIG:-~/.kube/config}
DIVIDER="════════════════════════════════════════════════════════════════"
SECTION() { echo -e "\n$DIVIDER"; echo "  $1"; echo -e "$DIVIDER"; }
SUB()     { echo -e "\n--- $1 ---"; }
CMD()     { echo -e "\n\$ $1"; eval "$1" 2>&1 || echo "[命令执行失败或无输出]"; }

echo "============================================"
echo "  赛博图书馆 · 集群诊断报告"
echo "  生成时间：$(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "  主机名：$(hostname)"
echo "============================================"

###############################################################################
SECTION "01 · 基础环境"
###############################################################################

SUB "WSL/OS 信息"
CMD "uname -a"

SUB "Docker 状态"
CMD "docker info --format '{{.ServerVersion}}' 2>/dev/null || echo 'Docker 未运行'"
CMD "docker version --format '{{.Server.Version}}' 2>/dev/null || true"

SUB "k3d 版本"
CMD "k3d version 2>/dev/null || echo 'k3d 未安装'"

SUB "kubectl 版本"
CMD "kubectl version --client --short 2>/dev/null || kubectl version --client 2>/dev/null || echo 'kubectl 未找到'"

SUB "Helm 版本"
CMD "helm version --short 2>/dev/null || echo 'Helm 未安装'"

SUB "GPU 状态（宿主机）"
CMD "nvidia-smi --query-gpu=name,memory.total,memory.used,utilization.gpu --format=csv,noheader 2>/dev/null || echo 'nvidia-smi 不可用'"

SUB "内存使用"
CMD "free -h"

SUB "磁盘使用"
CMD "df -h / /tmp ~/projects 2>/dev/null | sort -u"

###############################################################################
SECTION "02 · k3d 集群状态"
###############################################################################

SUB "k3d 集群列表"
CMD "k3d cluster list 2>/dev/null"

SUB "k3d 容器状态"
CMD "docker ps --filter name=k3d-ai-cluster --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null"

SUB "持久化配置文件"
CMD "ls -la ~/k3d-config/ 2>/dev/null || echo '目录不存在'"
CMD "cat ~/k3d-config/registries.yaml 2>/dev/null || echo 'registries.yaml 不存在'"

###############################################################################
SECTION "03 · K8s 节点"
###############################################################################

SUB "节点列表（含标签）"
CMD "kubectl get nodes -L node-role --no-headers 2>/dev/null && echo '' && kubectl get nodes -L node-role 2>/dev/null"

SUB "节点详情（资源容量）"
CMD "kubectl describe nodes 2>/dev/null | grep -E '(Name:|Role:|Capacity:|Allocatable:|cpu|memory|nvidia.com/gpu)' | head -60"

SUB "节点 Conditions"
CMD "kubectl get nodes -o custom-columns='NAME:.metadata.name,READY:.status.conditions[?(@.type==\"Ready\")].status,MEMORY:.status.conditions[?(@.type==\"MemoryPressure\")].status,DISK:.status.conditions[?(@.type==\"DiskPressure\")].status,PID:.status.conditions[?(@.type==\"PIDPressure\")].status' 2>/dev/null"

###############################################################################
SECTION "04 · 所有 Pod 状态"
###############################################################################

SUB "全部 Pod（所有命名空间）"
CMD "kubectl get pods -A -o wide 2>/dev/null"

SUB "非 Running/Succeeded 的 Pod（异常检查）"
CMD "kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded 2>/dev/null || echo '全部正常或命令不支持'"
CMD "kubectl get pods -A 2>/dev/null | grep -v -E '(Running|Completed|Succeeded)' | grep -v 'NAME' || echo '全部 Pod 正常'"

SUB "重启次数 > 0 的 Pod"
CMD "kubectl get pods -A -o jsonpath='{range .items[?(@.status.containerStatuses[0].restartCount>0)]}{.metadata.namespace}/{.metadata.name}  重启:{.status.containerStatuses[0].restartCount}  原因:{.status.containerStatuses[0].lastState.terminated.reason}  \n{end}' 2>/dev/null || echo '无重启 Pod 或 jsonpath 不支持'"

SUB "近期 Warning Events（最近 30 条）"
CMD "kubectl get events -A --field-selector type=Warning --sort-by='.lastTimestamp' 2>/dev/null | tail -30"

###############################################################################
SECTION "05 · Helm Releases"
###############################################################################

SUB "所有 Helm Release"
CMD "helm list -A 2>/dev/null"

SUB "monitoring-stack 详情"
CMD "helm status monitoring-stack -n monitoring 2>/dev/null || echo 'monitoring-stack 未找到'"
CMD "helm history monitoring-stack -n monitoring --max 5 2>/dev/null || true"

###############################################################################
SECTION "06 · 命名空间 & 资源概览"
###############################################################################

SUB "所有命名空间"
CMD "kubectl get namespaces 2>/dev/null"

SUB "所有 Deployment"
CMD "kubectl get deployments -A 2>/dev/null"

SUB "所有 StatefulSet"
CMD "kubectl get statefulsets -A 2>/dev/null"

SUB "所有 DaemonSet"
CMD "kubectl get daemonsets -A 2>/dev/null"

SUB "所有 Service"
CMD "kubectl get svc -A 2>/dev/null"

SUB "所有 Ingress"
CMD "kubectl get ingress -A 2>/dev/null"

SUB "所有 IngressRoute（Traefik CRD）"
CMD "kubectl get ingressroutes -A 2>/dev/null || echo 'IngressRoute CRD 不存在'"

SUB "所有 PVC"
CMD "kubectl get pvc -A 2>/dev/null"

SUB "所有 ServiceAccount（monitoring 相关）"
CMD "kubectl get sa -n monitoring 2>/dev/null"

SUB "所有 ClusterRole/ClusterRoleBinding（monitoring 相关）"
CMD "kubectl get clusterrole 2>/dev/null | grep -i -E '(monitor|prometheus|grafana|alert)' || echo '无匹配'"
CMD "kubectl get clusterrolebinding 2>/dev/null | grep -i -E '(monitor|prometheus|grafana|alert)' || echo '无匹配'"

###############################################################################
SECTION "07 · ArgoCD 状态"
###############################################################################

SUB "ArgoCD 命名空间 Pod"
CMD "kubectl get pods -n argocd 2>/dev/null"

SUB "ArgoCD Applications"
CMD "kubectl get applications -n argocd -o wide 2>/dev/null || echo 'ArgoCD CRD 不可用'"

SUB "ArgoCD Application 详情"
CMD "kubectl get applications -n argocd -o custom-columns='NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status,REVISION:.status.sync.revision' 2>/dev/null || true"

###############################################################################
SECTION "08 · Promtail & Loki（日志栈）"
###############################################################################

SUB "Promtail Pod 状态"
CMD "kubectl get pods -n logging -l app.kubernetes.io/name=promtail -o wide 2>/dev/null || kubectl get pods -n logging -o wide 2>/dev/null"

SUB "Loki Pod 状态"
CMD "kubectl get pods -n logging -l app.kubernetes.io/name=loki -o wide 2>/dev/null || true"

SUB "Loki 健康检查"
CMD "kubectl run loki-health-$$ --image=curlimages/curl --rm -i --restart=Never --timeout=30s -- curl -sf http://loki.logging.svc.cluster.local:3100/ready 2>/dev/null || echo 'Loki 不可达'"

SUB "Promtail 日志采集状态"
CMD "kubectl logs -n logging -l app.kubernetes.io/name=promtail --tail=5 --since=5m 2>/dev/null | tail -10 || echo '无日志'"

###############################################################################
SECTION "09 · Prometheus 核心验证（监控系统重点）"
###############################################################################

SUB "Prometheus Pod 状态"
CMD "kubectl get pods -n monitoring -l app.kubernetes.io/name=prometheus -o wide 2>/dev/null || kubectl get pods -n monitoring -o wide 2>/dev/null | grep -i prometheus"

SUB "Prometheus Service"
CMD "kubectl get svc -n monitoring 2>/dev/null | grep -i prometheus || true"

SUB "Prometheus 健康检查"
CMD "kubectl run prom-health-$$ --image=curlimages/curl --rm -i --restart=Never --timeout=30s -- curl -sf http://monitoring-stack-kube-prom-prometheus.monitoring.svc.cluster.local:9090/-/healthy 2>/dev/null || echo 'Prometheus /-/healthy 不可达'"

SUB "Prometheus 就绪检查"
CMD "kubectl run prom-ready-$$ --image=curlimages/curl --rm -i --restart=Never --timeout=30s -- curl -sf http://monitoring-stack-kube-prom-prometheus.monitoring.svc.cluster.local:9090/-/ready 2>/dev/null || echo 'Prometheus /-/ready 不可达'"

SUB "Prometheus 运行时信息"
CMD "kubectl run prom-buildinfo-$$ --image=curlimages/curl --rm -i --restart=Never --timeout=30s -- curl -sf 'http://monitoring-stack-kube-prom-prometheus.monitoring.svc.cluster.local:9090/api/v1/status/buildinfo' 2>/dev/null || echo '无法获取 buildinfo'"

SUB "Prometheus 抓取目标（完整列表 + 状态）"
CMD "kubectl run prom-targets-$$ --image=curlimages/curl --rm -i --restart=Never --timeout=30s -- curl -sf 'http://monitoring-stack-kube-prom-prometheus.monitoring.svc.cluster.local:9090/api/v1/targets' 2>/dev/null | python3 -m json.tool 2>/dev/null || kubectl run prom-targets-$$ --image=curlimages/curl --rm -i --restart=Never --timeout=30s -- curl -sf 'http://monitoring-stack-kube-prom-prometheus.monitoring.svc.cluster.local:9090/api/v1/targets' 2>/dev/null || echo '无法获取 targets'"

SUB "Prometheus 抓取目标摘要（仅 up/down）"
CMD "kubectl run prom-targets-summary-$$ --image=curlimages/curl --rm -i --restart=Never --timeout=30s -- sh -c \"curl -sf 'http://monitoring-stack-kube-prom-prometheus.monitoring.svc.cluster.local:9090/api/v1/targets' | python3 -c \\\" import sys,json; d=json.load(sys.stdin); targets=d.get('data',{}).get('activeTargets',[]); up=[t for t in targets if t.get('health')=='up']; down=[t for t in targets if t.get('health')!='up']; print(f'总计: {len(targets)} 个目标, UP: {len(up)}, DOWN/其他: {len(down)}'); print(); print('=== DOWN/UNHEALTHY ==='); [print(f\\\"  {t.get('scrapePool','?')} | {t.get('labels',{}).get('instance','?')} | {t.get('health')} | {t.get('lastError','')[:100]}\\\") for t in down]; print(); print('=== UP ==='); [print(f\\\"  {t.get('scrapePool','?')} | {t.get('labels',{}).get('instance','?')} | {t.get('health')}\\\") for t in up[:50]]; \\\"\" 2>/dev/null || echo '摘要生成失败，使用上一条完整列表'"

SUB "Prometheus 告警规则"
CMD "kubectl run prom-rules-$$ --image=curlimages/curl --rm -i --restart=Never --timeout=30s -- curl -sf 'http://monitoring-stack-kube-prom-prometheus.monitoring.svc.cluster.local:9090/api/v1/rules' 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); groups=d.get('data',{}).get('groups',[]); print(f'规则组数: {len(groups)}'); [print(f'  {g[\"name\"]} ({len(g.get(\"rules\",[]))} 条规则)') for g in groups]" 2>/dev/null || echo '无法获取告警规则'"

SUB "Prometheus 当前告警"
CMD "kubectl run prom-alerts-$$ --image=curlimages/curl --rm -i --restart=Never --timeout=30s -- curl -sf 'http://monitoring-stack-kube-prom-prometheus.monitoring.svc.cluster.local:9090/api/v1/alerts' 2>/dev/null | python3 -m json.tool 2>/dev/null || echo '无法获取当前告警'"

SUB "Prometheus 配置（前 100 行）"
CMD "kubectl run prom-config-$$ --image=curlimages/curl --rm -i --restart=Never --timeout=30s -- curl -sf 'http://monitoring-stack-kube-prom-prometheus.monitoring.svc.cluster.local:9090/api/v1/status/config' 2>/dev/null | python3 -c \"import sys,json; d=json.load(sys.stdin); cfg=d.get('data',{}).get('yaml',''); print(cfg[:5000])\" 2>/dev/null || echo '无法获取配置'"

SUB "Prometheus TSDB 状态"
CMD "kubectl run prom-tsdb-$$ --image=curlimages/curl --rm -i --restart=Never --timeout=30s -- curl -sf 'http://monitoring-stack-kube-prom-prometheus.monitoring.svc.cluster.local:9090/api/v1/status/tsdb' 2>/dev/null | python3 -m json.tool 2>/dev/null || echo '无法获取 TSDB 状态'"

###############################################################################
SECTION "10 · ServiceMonitor & PodMonitor"
###############################################################################

SUB "所有 ServiceMonitor"
CMD "kubectl get servicemonitors -A -o wide 2>/dev/null || echo 'ServiceMonitor CRD 不存在'"

SUB "所有 PodMonitor"
CMD "kubectl get podmonitors -A -o wide 2>/dev/null || echo 'PodMonitor CRD 不存在'"

SUB "ServiceMonitor 详细定义"
CMD "kubectl get servicemonitors -A -o yaml 2>/dev/null | head -200 || true"

SUB "Prometheus 的 ServiceMonitor Selector 配置"
CMD "kubectl get prometheus -n monitoring -o yaml 2>/dev/null | grep -A 20 'serviceMonitorSelector' || echo '无法获取 Prometheus CRD 配置'"

###############################################################################
SECTION "11 · Grafana 验证"
###############################################################################

SUB "Grafana Pod 状态"
CMD "kubectl get pods -n monitoring -l app.kubernetes.io/name=grafana -o wide 2>/dev/null || kubectl get pods -n monitoring -o wide 2>/dev/null | grep -i grafana"

SUB "Grafana Service"
CMD "kubectl get svc -n monitoring 2>/dev/null | grep -i grafana"

SUB "Grafana 健康检查"
GRAFANA_POD=(kubectl get pods -n monitoring -l app.kubernetes.io/name=grafana -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
if [ -n "$GRAFANA_POD" ]; then
    SUB "Grafana /api/health"
    CMD "kubectl exec -n monitoring $GRAFANA_POD -- wget -qO- http://localhost:3000/api/health 2>/dev/null || echo 'Grafana health 接口不可达'"

    SUB "Grafana 数据源列表"
    CMD "kubectl exec -n monitoring $GRAFANA_POD -- wget -qO- http://localhost:3000/api/datasources 2>/dev/null | python3 -m json.tool 2>/dev/null || echo '无法获取数据源'"

    SUB "Grafana Dashboard 列表"
    CMD "kubectl exec -n monitoring $GRAFANA_POD -- wget -qO- 'http://localhost:3000/api/search?type=dash-db' 2>/dev/null | python3 -c \"import sys,json; ds=json.load(sys.stdin); print(f'Dashboard 数量: {len(ds)}'); [print(f'  - {d[\\\"title\\\"]} (uid:{d.get(\\\"uid\\\",\\\"?\\\")})') for d in ds[:30]]\" 2>/dev/null || echo '无法获取 Dashboard 列表'"

    SUB "Grafana 组织信息"
    CMD "kubectl exec -n monitoring $GRAFANA_POD -- wget -qO- http://localhost:3000/api/org 2>/dev/null || echo '无法获取'"

    SUB "Grafana 插件列表（前 20 个）"
    CMD "kubectl exec -n monitoring $GRAFANA_POD -- wget -qO- http://localhost:3000/api/plugins 2>/dev/null | python3 -c \"import sys,json; d=json.load(sys.stdin); [print(f'  {p[\\\"name\\\"]} {p[\\\"info\\\"][\\\"version\\\"]}') for p in d[:20]]\" 2>/dev/null || echo '无法获取'"
else
    echo "Grafana Pod 未找到，跳过 API 检查"
fi

SUB "Grafana ConfigMap 内容"
CMD "kubectl get configmap -n monitoring 2>/dev/null | grep -i grafana || true"
CMD "kubectl get configmap -n monitoring -l app.kubernetes.io/name=grafana -o yaml 2>/dev/null | head -100 || true"

###############################################################################
SECTION "12 · Alertmanager 验证"
###############################################################################

SUB "Alertmanager Pod 状态"
CMD "kubectl get pods -n monitoring -l app.kubernetes.io/name=alertmanager -o wide 2>/dev/null || kubectl get pods -n monitoring -o wide 2>/dev/null | grep -i alertmanager"

SUB "Alertmanager 健康检查"
CMD "kubectl run am-health-$$$$ --image=curlimages/curl --rm -i --restart=Never --timeout=30s -- curl -sf http://monitoring-stack-kube-prom-alertmanager.monitoring.svc.cluster.local:9093/-/healthy 2>/dev/null || echo 'Alertmanager 不可达'"

SUB "Alertmanager 当前告警"
CMD "kubectl run am-alerts-$$ --image=curlimages/curl --rm -i --restart=Never --timeout=30s -- curl -sf http://monitoring-stack-kube-prom-alertmanager.monitoring.svc.cluster.local:9093/api/v2/alerts 2>/dev/null | python3 -m json.tool 2>/dev/null || echo '无法获取'"

SUB "Alertmanager 配置"
CMD "kubectl get secret -n monitoring 2>/dev/null | grep -i alertmanager || true"

###############################################################################
SECTION "13 · kube-state-metrics & Node Exporter"
###############################################################################

SUB "kube-state-metrics Pod 状态"
CMD "kubectl get pods -n monitoring -l app.kubernetes.io/name=kube-state-metrics -o wide 2>/dev/null || kubectl get pods -n monitoring -o wide 2>/dev/null | grep -i kube-state"

SUB "kube-state-metrics 指标端点"
CMD "kubectl run ksm-test-$$ --image=curlimages/curl --rm -i --restart=Never --timeout=30s -- curl -sf http://monitoring-stack-kube-prom-kube-state-metrics.monitoring.svc.cluster.local:8080/metrics 2>/dev/null | head -5 || echo 'kube-state-metrics 不可达'"

SUB "Node Exporter Pod 状态"
CMD "kubectl get pods -n monitoring -l app.kubernetes.io/name=node-exporter -o wide 2>/dev/null || kubectl get pods -n monitoring -o wide 2>/dev/null | grep -i node-exporter"

SUB "Node Exporter 节点覆盖"
CMD "kubectl get pods -n monitoring -l app.kubernetes.io/name=node-exporter -o custom-columns='NODE:.spec.nodeName,STATUS:.status.phase' 2>/dev/null || true"

###############################################################################
SECTION "14 · NVIDIA Device Plugin（GPU）"
###############################################################################

SUB "NVIDIA Device Plugin Pod"
CMD "kubectl get pods -n kube-system -l name=nvidia-device-plugin-ds -o wide 2>/dev/null || kubectl get pods -n kube-system -o wide 2>/dev/null | grep -i nvidia"

SUB "GPU 资源可分配量"
CMD "kubectl get nodes -o custom-columns='NODE:.metadata.name,GPU:.status.allocatable.nvidia\.com/gpu' 2>/dev/null || echo '无 GPU 资源信息'"

SUB "GPU RuntimeClass"
CMD "kubectl get runtimeclass 2>/dev/null || echo '无 RuntimeClass'"

###############################################################################
SECTION "15 · 网关（ai-infra-gateway）验证"
###############################################################################

SUB "网关 Pod 状态"
CMD "kubectl get pods -n ai-platform -o wide 2>/dev/null"

SUB "网关 Service"
CMD "kubectl get svc -n ai-platform 2>/dev/null"

SUB "网关健康检查"
CMD "kubectl run gw-health-$$ --image=curlimages/curl --rm -i --restart=Never --timeout=30s -- curl -sf http://ai-infra-gateway.ai-platform.svc.cluster.local:8000/health 2>/dev/null || echo '网关 /health 不可达'"

SUB "网关 /metrics（Prometheus 指标）"
CMD "kubectl run gw-metrics-$$ --image=curlimages/curl --rm -i --restart=Never --timeout=30s -- curl -sf http://ai-infra-gateway.ai-platform.svc.cluster.local:8000/metrics 2>/dev/null | grep -E '^gateway_' | head -20 || echo '网关 /metrics 不可达或无 gateway_ 指标'"

SUB "外部 Traefik 路由测试"
CMD "curl -sf -o /dev/null -w 'HTTP %{http_code}' http://localhost/gateway/health 2>/dev/null || echo 'localhost/gateway/health 不可达'"
CMD "curl -sf -o /dev/null -w 'HTTP %{http_code}' http://localhost/grafana/ 2>/dev/null || echo 'localhost/grafana/ 不可达'"
CMD "curl -sf -o /dev/null -w 'HTTP %{http_code}' http://localhost/prometheus/-/healthy 2>/dev/null || echo 'localhost/prometheus/-/healthy 不可达'"
CMD "curl -sf -o /dev/null -w 'HTTP %{http_code}' http://localhost/openwebui/ 2>/dev/null || echo 'localhost/openwebui/ 不可达'"

###############################################################################
SECTION "16 · Ollama & Open-WebUI"
###############################################################################

SUB "Ollama Pod 状态"
CMD "kubectl get pods -n ai-platform -l app.kubernetes.io/name=ollama -o wide 2>/dev/null || true"

SUB "Ollama 模型列表"
CMD "kubectl run ollama-models-$$ --image=curlimages/curl --rm -i --restart=Never --timeout=30s -- curl -sf http://ollama-0.ai-platform.svc.cluster.local:11434/api/tags 2>/dev/null | python3 -m json.tool 2>/dev/null || echo 'Ollama 不可达或无模型'"

SUB "Open-WebUI Pod 状态"
CMD "kubectl get pods -n ai-platform -l app.kubernetes.io/name=open-webui -o wide 2>/dev/null || true"

###############################################################################
SECTION "17 · Traefik 状态"
###############################################################################

SUB "Traefik Pod 状态"
CMD "kubectl get pods -n kube-system -l app.kubernetes.io/name=traefik -o wide 2>/dev/null || kubectl get pods -n kube-system -o wide 2>/dev/null | grep -i traefik"

SUB "Traefik Service（外部端口）"
CMD "kubectl get svc -n kube-system traefik 2>/dev/null || kubectl get svc -n kube-system 2>/dev/null | grep -i traefik"

SUB "Traefik IngressRoute 列表"
CMD "kubectl get ingressroutes -A 2>/dev/null || echo 'CRD 不存在'"

SUB "Traefik IngressRoute 详情"
CMD "kubectl get ingressroutes -A -o yaml 2>/dev/null | head -150 || true"

###############################################################################
SECTION "18 · PrometheusRules（告警规则 CRD）"
###############################################################################

SUB "所有 PrometheusRule"
CMD "kubectl get prometheusrules -A 2>/dev/null || echo 'PrometheusRule CRD 不存在'"

SUB "PrometheusRule 详情"
CMD "kubectl get prometheusrules -A -o yaml 2>/dev/null | head -200 || true"

###############################################################################
SECTION "19 · ConfigMap & Secret（monitoring 相关）"
###############################################################################

SUB "monitoring 命名空间所有 ConfigMap"
CMD "kubectl get configmap -n monitoring 2>/dev/null"

SUB "monitoring 命名空间所有 Secret"
CMD "kubectl get secret -n monitoring 2>/dev/null"

SUB "ai-platform 命名空间所有 ConfigMap"
CMD "kubectl get configmap -n ai-platform 2>/dev/null"

SUB "ai-platform 命名空间所有 Secret"
CMD "kubectl get secret -n ai-platform 2>/dev/null"

###############################################################################
SECTION "20 · Helm Values 完整内容"
###############################################################################

SUB "monitoring-stack Helm values（全部生效值）"
CMD "helm get values monitoring-stack -n monitoring -a 2>/dev/null | head -300 || echo 'monitoring-stack 未找到'"

SUB "monitoring-stack values 摘要（用户自定义值）"
CMD "helm get values monitoring-stack -n monitoring 2>/dev/null | head -100 || true"

###############################################################################
SECTION "21 · 网络连通性测试"
###############################################################################

SUB "CoreDNS 健康"
CMD "kubectl run dns-test-$$ --image=busybox --rm -i --restart=Never --timeout=15s -- nslookup kubernetes.default.svc.cluster.local 2>/dev/null || echo 'DNS 测试失败'"

SUB "Metrics Server 可达"
CMD "kubectl top nodes 2>/dev/null || echo 'metrics-server 不可用（kubectl top 报错）'"

SUB "集群内 Service 解析"
CMD "kubectl run svc-test-$$ --image=curlimages/curl --rm -i --restart=Never --timeout=15s -- curl -sf http://monitoring-stack-kube-prom-prometheus.monitoring:9090/-/healthy 2>/dev/null && echo 'Prometheus 集群内可达' || echo 'Prometheus 集群内不可达'"
CMD "kubectl run svc-test2-$$ --image=curlimages/curl --rm -i --restart=Never --timeout=15s -- curl -sf http://ai-infra-gateway.ai-platform:8000/health 2>/dev/null && echo 'Gateway 集群内可达' || echo 'Gateway 集群内不可达'"

###############################################################################
SECTION "22 · 关键路径检查"
###############################################################################

SUB "项目代码目录"
CMD "ls ~/projects/project/ 2>/dev/null | head -20 || echo '目录不存在'"

SUB "GitOps 工作区"
CMD "ls ~/projects/project/workspace/working-platform/ 2>/dev/null | head -20 || echo '目录不存在'"

SUB "网关源码"
CMD "ls -la ~/projects/project/ai-infra-gateway/01-gateway-server/gateway_server.py 2>/dev/null || echo '网关源码不存在'"

SUB "网关测试目录"
CMD "ls ~/projects/project/ai-infra-gateway/01-gateway-server/tests/ 2>/dev/null | head -20 || echo '测试目录不存在'"

SUB "Git 状态"
CMD "cd ~/projects/project && git status --short 2>/dev/null | head -20 || echo '不是 git 仓库'"
CMD "cd ~/projects/project && git log --oneline -5 2>/dev/null || true"

SUB "GitHub 远程仓库"
CMD "cd ~/projects/project && git remote -v 2>/dev/null || true"

###############################################################################
SECTION "23 · 接入层完整链路测试（从外部浏览器视角）"
###############################################################################

SUB "端口 80 是否监听"
CMD "ss -tlnp 2>/dev/null | grep ':80 ' || echo '端口 80 未监听'"

SUB "完整链路：浏览器 → Traefik → 各后端"
CMD "echo 'GET /gateway/health' && curl -v http://localhost/gateway/health 2>&1 | grep -E '(< HTTP|Connected|resolve|refused|timed out)' || echo '不可达'"
CMD "echo 'GET /grafana/' && curl -v http://localhost/grafana/ 2>&1 | grep -E '(< HTTP|Connected|resolve|refused|timed out)' || echo '不可达'"
CMD "echo 'GET /prometheus/-/healthy' && curl -v http://localhost/prometheus/-/healthy 2>&1 | grep -E '(< HTTP|Connected|resolve|refused|timed out)' || echo '不可达'"
CMD "echo 'GET /openwebui/' && curl -v http://localhost/openwebui/ 2>&1 | grep -E '(< HTTP|Connected|resolve|refused|timed out)' || echo '不可达'"

###############################################################################
SECTION "24 · 已知问题排查"
###############################################################################

SUB "ghcr.io 镜像问题排查（certgen pod）"
CMD "kubectl get pods -A 2>/dev/null | grep -i certgen || echo '无 certgen pod'"
CMD "kubectl get jobs -A 2>/dev/null | grep -i cert || echo '无 cert 相关 job'"

SUB "admission webhook 状态"
CMD "kubectl get validatingwebhookconfigurations 2>/dev/null | grep -i -E '(prom|monitor)' || echo '无 monitoring 相关 validating webhook'"
CMD "kubectl get mutatingwebhookconfigurations 2>/dev/null | grep -i -E '(prom|monitor)' || echo '无 monitoring 相关 mutating webhook'"

SUB "TLS Secret（monitoring-stack-kube-prom-admission）"
CMD "kubectl get secret monitoring-stack-kube-prom-admission -n monitoring -o yaml 2>/dev/null | head -20 || echo 'Secret 不存在（可能已清理或不需要）'"

###############################################################################
SECTION "诊断完成"
###############################################################################

echo ""
echo "============================================"
echo "  诊断报告生成完毕"
echo "  时间：$(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "============================================"
echo ""
echo "将此输出完整提供给 AI 助手，即可快速同步集群当前状态。"
echo ""echo -e "\n✅ 日志文件已生成：${LOG_FILE}"
echo "查看日志命令：cat ${LOG_FILE}"
