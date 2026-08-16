# CyberRouter v2 — 控制面调度器 + 数据面网关

> 面向 **8GB 单卡异构推理** 的 K8s Operator 调度器。策略做成 **RoutingPolicy CRD**（版本化/可灰度/GitOps），Operator 编译路由快照，网关执行——**"慢路径定策略，快路径执行"**（Envoy+xDS 模式）。
> 与 HeteroServe v3（服务面缓存优化）构成完整闭环：**CyberRouter 选"请求去哪"，HeteroServe 管"选定后如何高效运行"**。

## 核心数字（全部真实测量，2026-08-06）

| 指标 | 结果 |
|---|---|
| CRD 热更新生效 | **实测**：改 CR → 快照 hash 更新 → 网关 10s 加载新规则 |
| 决策引擎 | 复杂度→规则匹配→预判P99→候选过滤，**9/9 单测**，每决策带推理链 |
| 快照同步闭环 | Operator→ConfigMap→gateway **端到端实测**（短请求→CPU / 长请求→GPU 真实回答）|
| **预判性降级** | GPU 忙（P99≈1100ms > SLO）→ **主动降级 CPU tier**，不等失败重试（Demo 证据链）|
| 动态状态感知 | Operator 5s 采集 vLLM 指标（running/命中率/GPU 水位）→ 快照动态状态 |

## 架构

```
                    用户请求
                       │
                       ▼
        ┌───────────────────────────────┐
        │ Gateway (数据面·快路径)         │
        │  读快照 → 决策路由 → 转发后端    │
        │  预判降级 (GPU 忙→CPU)         │
        └───────────────┬───────────────┘
                        │ ConfigMap: cyberrouter-routing-snapshot
        ┌───────────────▼───────────────┐
        │ Operator (控制面·慢路径)       │
        │  Go + controller-runtime      │
        │  watch CRD → 编译快照          │
        │  5s 采集 vLLM 指标 → 动态状态   │
        └───────────────┬───────────────┘
                        │ watch
        ┌───────────────▼───────────────┐
        │ RoutingPolicy CRD (策略即代码) │
        │  rules: condition + action    │
        │  slo: low/medium/high P99     │
        └───────────────────────────────┘
```

## 关键设计（面试可讲）

### 1. 策略 CRD 化 — 改策略不改代码
```yaml
rules:
  - id: rule-001
    priority: 100
    condition:
      tokenCount: "< 200"            # 比较表达式字符串化
      cacheHitProbability: "> 0.5"   # cache-aware 联动点
    action:
      tier: ollama-service
      fallbackChain: [ollama-service, vllm-3b-service]
```

### 2. 预判性降级 — 排队模型预测 P99
```log
命中规则 rule-002 (complexity=high);
⚠️ 预判降级: vllm-3b-service (预判P99≈1100ms > SLO 800ms);
→ 降级到 ollama-service
```
`PredictP99 = 300ms + running × 边际延迟`（简化排队模型，诚实标注非完整 M/G/1）

### 3. Envoy+xDS 模式 — 快路径/慢路径分离
- 控制面（Operator）**5s 级**算策略（快照），数据面（网关）**逐请求**执行
- 网关不碰 CRD，只读 ConfigMap 快照 → 解耦、可增量同步（快照 sha256 hash）

## 快速开始（k3d 集群）

```bash
# 1. 部署 CRD + 示例策略
kubectl apply -f config/crd/bases/routing.cyberrouter.io_routingpolicies.yaml
kubectl apply -f config/samples/cost-first-default.yaml

# 2. 部署 Operator (in-cluster, 含指标采集)
kubectl apply -f ../workspace/working-platform/apps/cyberrouter/operator.yaml

# 3. 部署 gateway (挂载快照 ConfigMap)
kubectl apply -f ../workspace/working-platform/gateway/gateway-deployment.yaml

# 4. 验证
kubectl get rp cost-first-default           # Status.snapshotHash 被回写
kubectl get cm cyberrouter-routing-snapshot # 快照含静态规则 + 动态 tiers
```

## 目录结构

```
cyberrouter/
├── api/v1/              # RoutingPolicy CRD 类型 (types.go + deepcopy)
├── internal/
│   ├── controller/      # reconcile 循环 (watch CRD → 编译快照 → 写 ConfigMap)
│   ├── decision/        # 决策引擎 (复杂度/匹配/预判P99/候选过滤) — 纯逻辑可测试
│   ├── metrics/         # vLLM 指标采集 (running/命中率/GPU水位)
│   └── snapshot/        # 快照编译 (CR → 数据面 JSON + sha256 hash)
├── config/              # CRD manifests + 示例策略
└── main.go              # Operator 入口 (manager + 指标采集注入)
```

## 诚实边界（面试防雷）

- **预判 P99 是简化排队模型**（300ms + running×边际），非完整 M/G/1——主动说这是"第一版简化，深度后补"
- **cache-aware 动态加权已实现（2026-08-16）**：判定 tier 过载时用实测命中率打折预判 P99（`router.py` + Go `CacheAwareP99`），高命中 tier 更不易被降级。折扣系数 `cache_aware.boost`（默认 0.5）是**启发式初值，未经真实负载标定**——面试要主动说明这一点
- **灰度 (rollout.percentage)**：字段已定义，未做多版本流量拆分实测
- 单 GPU 环境：调度决策在"tier 间"，不做副本级弹性（单卡扩容=OOM，见 BLUEPRINT D3）

## 复现命令

```bash
# 决策引擎单元测试 (9/9)
go test ./internal/decision/...

# 指标解析测试 (4/4)
go test ./internal/metrics/...

# 本地跑 Operator (开发调试)
VLLM_METRICS_URL="http://vllm-3b-service.ai-platform.svc.cluster.local:8000/metrics" \
  KUBECONFIG=$(k3d kubeconfig write ai-cluster) go run .
```
