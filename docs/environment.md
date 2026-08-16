# Phase 0 环境交接文档 — 2026-08-04

> **用途**：明天或任何时候回来，读这一份 + `docs/BLUEPRINT.md`（蓝图）就能接上，无需重新喂上下文。
> **配套**：项目蓝图已移入 `docs/BLUEPRINT.md`（纳入 git 版本控制）。
> **本文件状态**：随环境变化持续更新。
>
> ## ✅ Phase 0-4 全部完成（2026-08-04 → 2026-08-06）
> Phase 0 环境 / Phase 1 HeteroServe（LMCache 3-5×+调参矩阵）/ Phase 2 CyberRouter（Go Operator+CRD+快照）/ Phase 3 联动（预判性降级 Demo）/ Phase 4 包装（两个 README+DoD 回填）。
> 推荐生产配置：**gpu=0.8 / max_num_seqs=16 / cpu_cache=1GB**。**下一步 = 深度解构代码 + 工程化思维 + 模拟面试深挖**。

---

## 一、当前状态快照

### 1.1 环境形态（三层嵌套，必须记住）

```
WSL2 (Windows) 
  └── Docker daemon (宿主机 docker, 有 daocloud 加速)
        └── k3d 集群 ai-cluster (3 节点容器)
              └── k8s pods (真正的应用)
```

**关键认知**：k8s 的 `hostPath` 指向**节点容器**的文件系统，不是 WSL 宿主。要把宿主机文件给 pod 用，需手动复制进节点（见 §3.3）。

### 1.2 集群与资源

| 项 | 值 |
|---|---|
| 集群 | `ai-cluster`（k3d v5.8.3, k3s v1.31.5） |
| 节点 | server-0（控制面 3g）、agent-0（GPU 节点 8g）、agent-1（CPU 节点 8g） |
| GPU | RTX 4070 Laptop 8GB，`nvidia.com/gpu: 1` 宣告在 agent-0 |
| 节点标签 | agent-0: `nvidia.com/gpu=true`；agent-1: `node-role=cpu` |

### 1.3 已部署内容（全部可用）

| 组件 | 位置 | 状态 |
|---|---|---|
| NVIDIA device plugin | kube-system | ✅ 宣告 nvidia.com/gpu |
| kube-prometheus-stack | monitoring | ✅ 20 targets up |
| Grafana | monitoring | ✅ HTTP 200, 密码 admin123 |
| vLLM 3B-AWQ (0.8/16) | ai-platform (`vllm-3b-service:8000`) | ✅ 推理通过, KV 123K tokens |
| Ollama 1.5b + 3b | ai-platform (`ollama-service:11434`) | ✅ 模型已导入 |

### 1.4 未部署（仓库里有定义但未用）

- gateway（ai-infra-gateway）— Phase 2 用
- loki / promtail（日志）— 可选，Phase 3 可观测闭环时用
- open-webui（聊天 UI）— 可选
- gpu-exporter / dcgm-exporter — **注意：nodeSelector 用的是 `node-role: gpu` 标签，当前 GPU 节点是 `nvidia.com/gpu=true`，标签不匹配，需先统一约定**

---

## 二、访问命令速查

```bash
# 集群
export KUBECONFIG=$(k3d kubeconfig write ai-cluster)
kubectl get nodes -o wide
kubectl get pods -A

# GPU 测试
kubectl apply -f /home/fei/projects/project/k3d/manifests/gpu-test-pod.yaml
kubectl logs gpu-test

# vLLM 推理
kubectl port-forward -n ai-platform svc/vllm-service 8000:8000 &
curl http://localhost:8000/v1/models

# Ollama 推理
kubectl port-forward -n ai-platform svc/ollama-service 11434:11434 &
curl http://localhost:11434/api/tags

# Grafana（admin / admin123）
kubectl port-forward -n monitoring svc/monitoring-grafana 3000:80 &
```

---

## 三、关键配方（血泪调试结论，务必保留）

### 3.1 WSL2 容器内 GPU 四件套（vLLM/任何 CUDA 应用）

```
1. privileged: true          # 绕过 cgroup 设备限制访问 /dev/dxg
2. 挂载 /dev/dxg             # WSL2 的 GPU 设备（不是 /dev/nvidia*）
3. 驱动库保持原始目录结构挂载  # /usr/lib/wsl/drivers/nvtfi.inf_amd64_b747199a5b009127
                             #   → 必须保持版本子目录结构！libcuda loader 依赖它
4. LD_LIBRARY_PATH 指向:
   /usr/lib/wsl/drivers/nvtfi.inf_amd64_b747199a5b009127:/usr/lib/wsl/lib:/usr/local/nvidia/lib64
```

**额外**：vLLM 还需 `VLLM_WSL2_ENABLE_PIN_MEMORY=1`（vLLM 在 WSL2 默认禁用 pinned memory，而 UVA 需要它，vLLM 源码显式处理）。

**扁平挂载（把版本目录直接挂到 /usr/lib/wsl/drivers）只能让 nvidia-smi 工作，CUDA 计算会失败（cuInit 报 no driver）。**

参考实现：`k3d/manifests/nvidia-device-plugin.yaml`、`workspace/working-platform/apps/ai-platform/vllm.yaml`

### 3.2 镜像拉取策略（China 网络）

| 镜像源 | 状态 | 处理 |
|---|---|---|
| docker.io | daocloud 加速 | 集群 containerd 走 registries.yaml 镜像 |
| registry.k8s.io | 被墙 | `k8s.m.daocloud.io` 代理 or 手动导入 |
| quay.io | 慢但通 | 手动导入更稳 |
| ghcr.io | `ghcr.m.daocloud.io` 代理 403 | 不要用该代理；ghcr.io 直拉可用 |
| registry.ollama.ai | 极慢 | 弃用，改用 hf-mirror 下载 GGUF 本地导入 |

**手动导入大镜像**（k3d image import 有 bug）：
```bash
docker pull k8s.m.daocloud.io/<repo>:<tag>   # 或 quay.m.daocloud.io
docker tag k8s.m.daocloud.io/<repo>:<tag> <原始registry>/<repo>:<tag>
docker save <原始名> -o /tmp/img.tar
docker cp /tmp/img.tar <节点>:/tmp/img.tar
docker exec <节点> sh -c 'ctr -n k8s.io images import /tmp/img.tar && rm -f /tmp/img.tar'
```

### 3.3 宿主机文件给 pod（hostPath 陷阱）

```bash
# 模型在宿主机 /home/fei/models，节点容器里没有。需复制进 GPU 节点：
docker cp /home/fei/models k3d-ai-cluster-agent-0:/home/fei/
# 注意: 若目标目录已存在会嵌套成 models/models，先检查再 mv 修正
```

**已知已复制进 agent-0 的内容**：`/home/fei/models/qwen2.5-7b-awq`（5.2GB）
> ⚠️ 节点容器是临时性的，重建集群后需重新复制模型。

### 3.4 Ollama 模型导入（绕过被墙的 registry）

```bash
# 1. 从 hf-mirror 下载 GGUF (快, ~4MB/s)
curl -sL -o /home/fei/models/qwen2.5-1.5b-instruct-q4_k_m.gguf \
  "https://hf-mirror.com/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf"

# 2. 复制进 ollama pod + 本地导入
kubectl cp <gguf> ai-platform/<pod>:/tmp/model.gguf
kubectl exec -it <pod> -- sh -c 'echo "FROM /tmp/model.gguf" > /tmp/Modelfile && ollama create qwen2.5:1.5b -f /tmp/Modelfile'
```

---

## 四、已知问题与坑

1. **CPU tier 很慢**：Ollama 1.5b ≈ 0.7 token/s（CPU 限 2 核）。这是 Phase 1 调优/调度的对比基线，调度器应默认把延迟敏感请求导向 GPU。
2. **CDI spec 过期**：宿主机 `/etc/cdi/nvidia.yaml` 引用的驱动目录 `nvtf.inf_amd64_f7df59a98b1aeb40` 不存在（真实的是 `nvtfi.inf_amd64_b747199a5b009127`）。所以 `docker run --gpus all` 不可靠，**必须用手动挂载配方**（§3.1）。
3. **k3d image import 有 bug**（v5.8.3，"content digest not found"）→ 用手动 docker save/ctr import。
4. **kube-prometheus-stack**：webhook TLS secret 需手动补，且 secret 键名必须是 `cert`/`key`（不是 tls.crt/tls.key）。
5. **GPU 资源死锁**：Error 状态 pod 会一直占着 nvidia.com/gpu 不放，导致新 pod 调度失败。出现"0/3 nodes available: Insufficient nvidia.com/gpu"时，先删 Error pod。
6. **gpu-exporter/dcgm-exporter 的 nodeSelector 标签是 `node-role: gpu`，与当前 GPU 节点标签 `nvidia.com/gpu=true` 不一致** —— 部署前需统一。
7. **单 GPU 滚动更新死锁**：`replicas=1` + 默认 RollingUpdate（`maxUnavailable` 取整 = 0）会**先建新 pod 再删旧的**，新 pod 等 GPU 时旧 pod 不释放 → 永远 Pending。解法：`vllm-3b` 已改 **Recreate** 策略（先删后建）；手动等价操作 = `kubectl delete pod` 强制重建（tuning_matrix.py 的 apply_config 就是这么做的）。
8. **kubectl port-forward 就绪需要探测**：起 port-forward 后 `sleep 4s` 不够，首个请求会 connection refused。必须先轮询 `/v1/models` 通再发压测（tuning_matrix.py 已内置 30s 探测）。

---

## 五、GitOps 结构与工作模式（2026-08-04 定稿）

```
project/ (git 仓库 ai-infra)
├── docs/                    ← BLUEPRINT.md + environment.md（项目文档）
├── k3d/                     ← 集群配置 (registries.yaml + manifests/)
│   └── manifests/           ← nvidia-device-plugin.yaml, gpu-test-pod.yaml
├── ai-infra-gateway/        ← 数据面 gateway 代码 (Phase A 基线)
│   └── 01-gateway-server/
├── scripts/                 ← cluster-diagnostic.sh（工具脚本）
└── workspace/working-platform/  ← 部署 manifests（app-of-apps 结构）
    ├── bootstrap/           ← 预留的 ArgoCD Application（dormant，Phase 4 用）
    ├── gateway/             ← gateway manifests
    ├── monitoring-stack/    ← kube-prometheus-stack values
    └── apps/                ← 各应用 (ai-platform, loki, promtail, exporters)
```

### 工作模式决策（已定稿）

**✅ 采用：Git 即源 + kubectl apply**
**⏸️ 暂缓：ArgoCD 监听/自愈同步（不部署）**

理由：
- 开发期自愈会回滚实验性改动，束手束脚（个人单机没必要）
- `bootstrap/` 下的 ArgoCD Application 定义保留为 **dormant**，作为 Phase 4 简历包装时的素材
- **提交纪律**：所有 manifest 变更 → git add/commit/push，然后 kubectl apply

### 2026-08-04 目录清理记录

- 🗑️ 删除：`diagnostic-20260727-193135.log`、`gateway_server.py.v9.bak`、缓存目录
- 📦 移出 git → `/home/fei/notes/`：`AI_INFRA_DEVELOPMENT_LOG.md`、`教材.md`（学习资料不入库）
- 📂 归位：`cluster-diagnostic.sh` → `scripts/`
- 🆕 新建：`.gitignore`（缓存/日志/备份）、`docs/`（蓝图移入）

---

## 六、Phase 1 进度（HeteroServe v3 骨架）

### ✅ 已完成（2026-08-05 → 2026-08-06）

1. **LMCache 接入**（H1）：vLLM 0.26 镜像内置 lmcache 0.5.2。接入方式 = `LMCACHE_CONFIG_FILE` + `--kv-transfer-config '{"kv_connector":"LMCacheConnectorV1","kv_role":"kv_both"}'`。
   - **踩坑**：8GB 上 LMCache 连接器需 ~1.3GB 显存，7B 塞不下 → **换 3B-AWQ**（主因）
   - **踩坑**：OOM → `max_local_cpu_size` 4GB→1GB + `use_gds: False`（WSL2 不支持 cufile）
   - **踩坑**：GPU KV 容量有限 → 需要长互异前缀才能触发逐出（见 benchmark）
2. **GPU 层换 3B**：vLLM-3B（gpu_memory_utilization 0.55 → 已按矩阵调至 **0.8**，max-model-len 5000，enforce-eager）。**7B 已下线**（8GB 塞不下 LMCache）。
3. **0.5B GPU 实例已砍**（设计决策）：启发式复杂度评估已够，8GB 拥挤。模型文件保留在 `/home/fei/models/qwen2.5-0.5b-awq`。
4. **前缀复用 benchmark**（H3）：`heteroserve/benchmark/benchmark.py`
   - 场景：20 个互异 4000-token 前缀（80K > GPU KV 65K）
   - **基线（无 LMCache）**：被逐出前缀 warm TTFT = **0.60s**（重新 prefill）
   - **LMCache 版**：= **0.11-0.19s**（CPU/SSD 恢复）→ **3-5× 提速**
   - 未逐出前缀两者均 0.04s（vLLM GPU 缓存）
   - **诚实口径**：LMCache 增量价值在"GPU KV 超容量"场景，非替代 vLLM 缓存
5. **缓存指标入 Prometheus**（H4）：vllm-servicemonitor（`release: monitoring`），命中率 = `prefix_cache_hits/queries_total`，Phase 2 调度器数据源。（旧记录"实测 27.6%"已撤：无复现来源、与共享前缀负载实测的 95.1% 口径不一致，见 `P1_验证发现.md` §命中率口径）
6. **H2 vLLM 调参矩阵**（2026-08-06 完成）：`heteroserve/benchmark/tuning_matrix.py` + `load_test.py`，全自动扫 `gpu_memory_utilization × max_num_seqs × max_local_cpu_size`。结果存 `heteroserve/benchmark/results/matrix_results.json`。
   - **A 系列（gpu_util, seqs=8, cpu=1GB）**：0.7→QPS 9.78；**0.8→QPS 9.87, TTFT P95 0.59s（最优）**；**0.9→OOM**（LMCache 连接器被 vLLM 预分配挤爆）
   - **B 系列（max_num_seqs, gpu=0.8, cpu=1GB）**：4→QPS 5.57, P95 1.27s（**旧 BASE，最大瓶颈**）；8→9.87；**16→10.23（峰值）**；32→9.26（饱和回落）
   - **C 系列（max_local_cpu_size, gpu=0.55 固定保证逐出）**：1GB→逐出恢复中位 **0.113s**；2GB→0.126s（**当前负载 1GB 已够**）；**4GB→OOM ×3 确认**（6Gi pod limit 撑不住）
   - **推荐生产配置 = gpu 0.8 + max_num_seqs 16 + cpu_cache 1GB**：相对旧 BASE QPS **2.45×**、TTFT P95 **降 5.3×**（3.59s→0.68s）
   - **副作用**：gpu 0.55→0.8 使 GPU KV 容量 **65K→123K 翻倍**（更多前缀命中 GPU 热层，LMCache 逐出场景更难触发）

### ⏳ 剩余（可选）

- **H6 观测面板**：Grafana GPU/缓存/LLM 面板（可留到 Phase 3 联动闭环一起做）
- **HeteroServe README**（见 heteroserve/README.md，已含 benchmark 小结）

### 深挖区学习顺序（Blueprint §6.1）
vLLM KV 机制 → LMCache 集成点 → 排队模型 → Operator。

---

## 七、Phase 2 进度（CyberRouter v2 Operator）

### ✅ 已完成（2026-08-06）

1. **P2-1 Go Operator 骨架 + RoutingPolicy CRD 跑通**：
   - **语言决策**：Go + controller-runtime（非 kopf）。正宗 Operator 就该 Go，简历杀伤力大。
   - **Go 工具链**：`~/.local/go`（go 1.26 由 go.mod 自动下载），`GOPROXY=https://goproxy.cn`（proxy.golang.org 被墙）。controller-gen v0.16.5 在 `~/.local/go/bin`。
   - **项目结构**：`project/cyberrouter/`（api/v1 CRD 类型 + internal/controller reconcile + main.go + config/）
   - **验证**：CRD 部署 ✅ + `cost-first-default` 示例 CR ✅ + reconcile 闭环 ✅ + Status 回写（observedGeneration/snapshotHash）✅

2. **P2-2 快照同步**（ab63854）：`internal/snapshot` 编译快照（CR → 数据面 JSON，规则按优先级降序）+ sha256 hash。Controller reconcile 幂等写 `cyberrouter-routing-snapshot` ConfigMap（存在则更新，删除策略时清理）。验证：ConfigMap 生成 + Status 真实 hash `1b07f181...`。
3. **P2-3 决策引擎**（5ece079）：`internal/decision` 纯逻辑包，**9/9 单元测试通过**。
   - 复杂度评估（token → low/med/high）、规则匹配（条件表达式解析）、SelectTier（返回可解释推理链 Reasons）
   - `PredictP99` 简化排队模型（当前 P99 + 队列深度×边际延迟）、`FilterCandidates` 候选过滤（剔除不健康/超 SLO，输出拒绝理由）
   - 动态状态（健康度/P99/队列）由 Operator 从 Prometheus 采集填充（Phase 3 接真实数据）
4. **P2-4 gateway 数据面改造**（641c057）：gateway v11.1。
   - `snapshot_loader.py`：ConfigMap 挂载快照加载 + 10s 后台刷新
   - `router.py`：决策路由（Python，与 Go decision 语义一致）
   - `gateway_server.py`：`route_request` 快照优先，fallback 到原 get_backend；决策日志
   - 部署：gateway v12 镜像 + deployment 挂载快照 ConfigMap
5. **端到端闭环验证**（2026-08-06，**全部真实回答**）：
   - **CRD 热更新实测** ✅：改 CR（tokenCount <100→<200）→ Operator reconcile（gen 1→2）→ 快照 hash 更新 `1b07f181`→`897092e7` → gateway 10s 内加载新规则
   - **场景1** [短+高命中] → **ollama-service 真实回答**（`/api/chat/stream`，rule-001）
   - **场景2** [2310 tokens] → **vllm-3b-service 真实回答**（`/api/generate`，rule-002 high，model 映射为 `/models/qwen2.5-3b-awq`）
   - **完整链路**：Operator 编译快照 → ConfigMap → gateway 挂载 → 决策路由 → model 映射 → 后端真实推理 ✅
   - **修的两个 bug**：① snapshot_loader 用 mtime 判断刷新不生效（K8s ConfigMap 挂载是 symlink 原子替换）→ 改内容 hash 比较；② vllm 分支用局部变量 model 而非映射后的 body.model → 转发 404 → 改 `body.get("model")`

### ⏳ 遗留（低优先级 / Phase 3）

- **ollama `/api/generate` messages 兼容**（Phase A 遗留）：generate 端点 ollama 分支把含 messages 的 body 转发 ollama `/api/generate`（要 prompt）→ response 空。`/api/chat/stream`（走 `/api/chat` 支持 messages）完整工作。修法：ollama 分支 messages→prompt。
- **预判性降级端到端**：FilterCandidates 逻辑就绪，接真实 Prometheus 指标后演示主动降级（Phase 3 cache-aware 联动）。

### 踩坑（Go 工具链）

1. **controller-gen v0.16.5 在 go1.26 下 crd 生成器输出空**（group/kind 全空），且 object 生成器只生成 root 类型 deepcopy → **CRD yaml 手写 + deepcopy 手写补齐**（见 cyberrouter/api/v1/zz_generated.deepcopy.go 注释）
2. **go.mod go 1.26 自动触发 toolchain 下载**（GOTOOLCHAIN=auto 默认），可能和系统 Go 版本不一致——用 `go version` 确认实际版本

---

## 八、Phase 3 进度（联动闭环）

### ✅ 已完成（2026-08-06）

1. **P3-1 Operator 指标采集 + 快照动态状态**：
   - `internal/metrics`：从 vLLM /metrics 采集（GPU 水位 / LMCache 命中率 / **num_requests_running**），简化排队模型 `PredictP99`（300ms + running×边际）
   - `Snapshot.Tiers` 动态状态：Operator 用 `RequeueAfter 5s` 定期刷新（M4 集群状态感知）
   - **Operator in-cluster 部署**：SA + RBAC + Deployment + `VLLM_METRICS_URL` env（集群内 Service DNS 采集）
2. **P3-2 预判性降级 + 动态决策**：gateway 决策读 tiers 动态状态——tier 不健康或预判 P99 超 SLO → 沿 fallbackChain 降级。修复：SLO 结构兼容（int vs dict）、waiting 恒 0 改用 running。
3. **P3-3 预判性降级 Demo（真实负载触发，证据链完整）**：
   - 40 并发长请求压 GPU → vllm `running=10` → Operator 采集 `P99≈1100ms`
   - gateway 决策日志：`命中 rule-002 (complexity=high); ⚠️ 预判降级: vllm-3b-service (预判P99≈1100ms > SLO 800ms); → 降级到 ollama-service`
   - **证明**：rule-002 本该去 vllm，但 GPU 忙时主动降级到 CPU tier（不等失败重试）

### 踩坑（Phase 3）

1. **vLLM WSL2 下 `num_requests_waiting` 恒为 0**（continuous batching 内部消化）→ 用 `num_requests_running`（并发执行数）作压力信号
2. **kubelet ConfigMap 挂载同步有延迟（~1min）**：Operator 5s 更新 ConfigMap，gateway 的挂载文件感知滞后 → 动态状态更新约 1 分钟后生效（Demo 需等同步窗口）

---

## 九、H6 观测面板 — ✅ 2026-08-08 完成（Phase 0 遗留补齐）

4 类 16 面板 `HeteroServe H6 — 推理观测`（ConfigMap: `heteroserve-h6-dashboard`，namespace monitoring）：
- **推理**：num_requests_running / KV cache 使用率 / gateway QPS / TTFT P95 / TPOT P95 / 生成吞吐
- **GPU**：利用率 / 显存 used-total / 温度 / 功耗（数据源 gpu-exporter）
- **缓存**：LMCache 命中率（`vllm:external_prefix_cache_*`）/ vLLM 前缀命中率 / 缓存 token 速率
- **调度**：决策分布 by tier / by source / 降级次数 / 决策趋势（`gateway_decisions_total` / `gateway_downgrades_total`）

### H6 前置修复的 4 个坑（全是 GitOps/标签坑，面试可讲）

1. **gpu-exporter WSL 配方**：NVML 需要版本驱动目录 + `/dev/dxg`。正确配方 = 挂 `/usr/lib/wsl/drivers/nvtfi.inf_amd64_b747199a5b009127`（保持版本子目录）+ `/dev/dxg` + `LD_LIBRARY_PATH` 指向版本目录（照抄 vLLM §3.1）。原来只挂 `/usr/lib/wsl/lib` → **NVML Shared Library Not Found**（`/usr/lib/wsl/lib` 只有 libdxcore，libnvidia-ml 在版本驱动目录里）
2. **ServiceMonitor label 必须 `release: monitoring`**：gpu-exporter 原来写 `monitoring-stack` → 被 Prometheus CR selector（`serviceMonitorSelector: {release: monitoring}`）忽略，**GPU 指标从未被采集过**（这是 gpu-exporter 一直"没数据"的真正原因）
3. **gateway ServiceMonitor 从未 apply**（P2 遗留）：文件在仓库但集群里没有 → gateway 指标从未进 Prometheus。`kubectl get servicemonitor -A` 核对，不在就 apply
4. **gateway 决策指标缺失**：原来只有请求计数，无调度决策/降级指标 → 新增 `gateway_decisions_total{tier,source}`（rule/fallback 都计）+ `gateway_downgrades_total{from_tier,to_tier}`（gateway v18，router.py 打 `downgraded_from` 标记）

### 其他 H6 相关要点

- **vLLM 指标名是 `vllm:`（冒号）不是 `vllm_`（下划线）**——PromQL 直接写 `vllm:num_requests_running` / `vllm:external_prefix_cache_hits_total` 等
- **决策指标触发**：请求带 `cache_hit_probability` 字段（>0.5 命中 rule-001 走 CPU）；不带则 cache_hit_prob=0.0 常走 fallback（设计如此，cache-aware 加权是演进项）
- **k3d 集群重启后**：`k3d cluster start ai-cluster`；vLLM 自动重建（新 pod 加载 ~2min）；删旧 Error pod（占 GPU 资源坑 §4.5）

---

## 修订记录

| 版本 | 日期 | 内容 |
|---|---|---|
| v1.0 | 2026-08-04 | Phase 0 完成：集群+GPU+监控+双后端，全部配方与踩坑 |
| v1.1 | 2026-08-06 | 补坑：单GPU滚动更新死锁(Recreate)、port-forward需探测；Phase 1 调参矩阵工具入库 |
| v1.2 | 2026-08-06 | **Phase 1 完成**：H2 调参矩阵入档（推荐 0.8/16/1GB，QPS 2.45×，P95 降 5.3×）+ vllm-3b 落推荐配置（KV 65K→123K） |
| v1.3 | 2026-08-06 | **Phase 2 启动**：P2-1 Go Operator 骨架 + RoutingPolicy CRD 跑通（语言决策 Go、工具链、踩坑） |
| v1.4 | 2026-08-06 | **Phase 2 核心完成**：P2-1→P2-4 + 端到端闭环验证（Operator→ConfigMap→gateway→决策路由）。遗留：model 名映射 gap、预判降级端到端（Phase 3） |
| v1.5 | 2026-08-06 | **Phase 2 完整收尾**：CRD 热更新实测 + model 映射修复（v14，vllm 真实回答）+ snapshot 刷新 hash 修复 + ollama chat/stream 端到端。遗留：ollama generate messages 兼容、预判降级（Phase 3） |
| v1.6 | 2026-08-06 | **Phase 3 完成**：联动闭环。Operator 指标采集→快照动态状态→预判性降级 Demo（GPU 忙 P99≈1100>800 → 主动降级 ollama，证据链完整）。踩坑：waiting 恒 0 用 running、kubelet 挂载延迟 |
| v1.7 | 2026-08-06 | **Phase 4 包装完成**：两个 README（cyberrouter/heteroserve）+ BLUEPRINT DoD/量化目标回填。全部 Phase 完成，进入深度解构+面试深挖阶段 |
| v1.8 | 2026-08-08 | **H6 观测面板完成**（Phase 0 遗留补齐）：GPU/推理/缓存/调度 4 类 16 面板。前置修复：gpu-exporter WSL 配方（NVML 版本目录+/dev/dxg）+ SM label 改 monitoring + gateway SM 补部署 + gateway 决策指标（v18，含 fallback）。集群已重启（08-08） |
