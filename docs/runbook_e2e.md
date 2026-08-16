# ai-cluster 端到端 runbook（含故障复盘）

> 用途：集群故障定位 → 修复/重建 → 部署 → e2e 验证 → 复现 benchmark 关键数字。
> 状态：2026-08-16 建立。集群 Down 的根因已定位（下 §1），修复步骤见 §2。
> 关联：[environment.md](./environment.md)（WSL2 GPU 配方/坑全集）、[P1_验证发现.md](./P1_验证发现.md)。

---

## 1. 故障复盘：集群为什么"没了"（2026-08-16 定位）

### 1.1 现象

```bash
$ k3d cluster list
NAME         SERVERS   AGENTS   LOADBALANCER
ai-cluster   0/1       0/2      true          # ← server/agent 全 Down，只剩 LB

$ docker ps -a --filter "name=k3d-ai-cluster"
k3d-ai-cluster-server-0   Exited (127) 4 days ago   # 2026-08-11 21:30:35
k3d-ai-cluster-agent-0    Exited (127) 4 days ago
k3d-ai-cluster-agent-1    Exited (127) 4 days ago
```

退出码 127（start 时报 mount 失败）：
```
failed to fulfil mount request: open /usr/lib/wsl/drivers/nvtf.inf_amd64_f7df59a98b1aeb40/libcuda.so.1.1: no such file or directory
```

### 1.2 根因链（三条证据）

| # | 证据 | 命令 | 结论 |
|---|---|---|---|
| 1 | 旧驱动目录消失、新目录出现 | `ls /usr/lib/wsl/drivers/` | `nvtf.inf_amd64_f7df59a98b1aeb40` 没了，变成 `nvtfi.inf_amd64_b747199a5b009127`（**Windows 侧 NVIDIA 驱动更新改了目录名**） |
| 2 | `/etc/cdi/nvidia.yaml` 仍指向旧目录 | `grep -c nvtf... /etc/cdi/nvidia.yaml` | **18 处**旧路径引用；CDI spec 过期 |
| 3 | k3d node 创建时带了 `--gpus all` | `docker inspect k3d-ai-cluster-agent-0 --format '{{json .HostConfig.DeviceRequests}}'` | `[{"Capabilities":[["gpu"]],...}]` → Docker 靠 CDI spec 注入驱动文件 |

**链条**：Windows 驱动更新改目录名 → `/etc/cdi/nvidia.yaml` 里 18 处路径失效 → k3d node（`--gpus all`）在**容器 start 时**按 CDI spec 注入 `/usr/lib/wsl/drivers/<旧目录>/libcuda.so.1.1` → 路径不存在 → OCI create 失败 → **exit 127 → 整个集群起不来**。

### 1.3 为什么不是 benchmark 数据丢了

- 数字没有丢：`heteroserve/benchmark/results/matrix_results.json`、`metrics_*.csv` 都是**提交进 git 的文件**，与集群生命周期无关。
- 丢的是"能复现它们的运行环境"——所以复现 95.1% / TTFT 3-5× 之前必须先把集群救回来。
- **面试话术**（诚实版）：集群 Down ≠ 数据丢失。数据在 git 里可复现；环境需要一次"WSL2 驱动改名 → CDI spec 失效"的诊断修复，这本身就是个很好的案例（断点诊断：exit 127 → 报错路径 → 目录 diff → CDI 引用比对）。

---

## 2. 修复路径

### 路径 A：廉价修复（推荐先试，不删集群）

CDI 注入是 **start 时按当前 spec 解析**的（`.Mounts` 里并没有固化驱动路径），所以改对 CDI 文件即可，无需重建。

```bash
# A0. 备份 + 替换 18 处旧目录 → 新目录（需 sudo）
sudo cp /etc/cdi/nvidia.yaml /etc/cdi/nvidia.yaml.bak-20260816
sudo sed -i 's/nvtf\.inf_amd64_f7df59a98b1aeb40/nvtfi.inf_amd64_b747199a5b009127/g' /etc/cdi/nvidia.yaml
grep -c nvtf.inf_amd64_f7df59a98b1aeb40 /etc/cdi/nvidia.yaml   # 期望 0
grep -c nvtfi.inf_amd64_b747199a5b009127 /etc/cdi/nvidia.yaml  # 期望 18

# A1. 启动集群
k3d cluster start ai-cluster

# A2. 等节点就绪
export KUBECONFIG=~/.kube/config
kubectl wait --for=condition=Ready nodes --all --timeout=120s
kubectl get nodes -L node-role
```

如果 A1 后节点还是 127：说明这台 Docker 把 CDI 结果固化进了容器配置（非 start 时解析），走**路径 B**。或尝试重新生成 CDI spec 后重试 A1：
```bash
sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
```

### 路径 B：重建集群（破坏性，删旧建新）

`~/k3d-config/rebuild.sh` 已把全部步骤写成脚本（创建 → 等节点 → 打标签 → ArgoCD → monitoring → 网关镜像 → 同步），但它会 `k3d cluster delete ai-cluster`，**节点容器是临时的，模型/镜像全部重新导入**。执行前确认可接受。

```bash
bash ~/k3d-config/rebuild.sh
```

**重建后必做的补漏**（rebuild.sh 不覆盖的）：

1. **CDI spec 必须已修好**（§2 路径 A 的 sed 或 `nvidia-ctk cdi generate`）——否则 `--gpus 'all'` 在 create 时就会 127，重建白搭。
2. **网关镜像 tag**：rebuild.sh 构建并导入 `ai-infra-gateway:latest`，但 `gateway-deployment.yaml` 引用的是 `ai-infra-gateway:v18`。需统一（历史做法见下）：
   ```bash
   cd ~/projects/project/ai-infra-gateway && docker build -t ai-infra-gateway:v18 .
   k3d image import ai-infra-gateway:v18 -c ai-cluster
   kubectl set image deployment/ai-infra-gateway -n ai-platform gateway=ai-infra-gateway:v18
   ```
3. **模型重新复制进 GPU 节点**（hostPath 陷阱：路径相对的是 node 容器，不是宿主机）：
   ```bash
   # 3B 是 benchmark 主角；7B 已下线但文件仍在 /home/fei/models
   docker cp /home/fei/models/qwen2.5-3b-awq k3d-ai-cluster-agent-0:/home/fei/models/
   # 若目标目录已存在会嵌套成 models/models，先检查再 mv 修正
   ```
4. **大镜像手动导入**（k3d image import 有 bug，v5.8.3 "content digest not found"）：
   ```bash
   docker pull k8s.m.daocloud.io/<repo>:<tag>   # 或 quay.m.daocloud.io
   docker tag k8s.m.daocloud.io/<repo>:<tag> <原始registry>/<repo>:<tag>
   docker save <原始名> -o /tmp/img.tar && docker cp /tmp/img.tar <节点>:/tmp/img.tar
   docker exec <节点> sh -c 'ctr -n k8s.io images import /tmp/img.tar && rm -f /tmp/img.tar'
   ```

---

## 3. 部署确认

```bash
export KUBECONFIG=~/.kube/config

# 节点与标签（agent-0 = GPU，agent-1 = CPU）
kubectl get nodes -L node-role,nvidia.com/gpu

# GPU 资源可调度（关键：vLLM pod 要 nvidia.com/gpu:1）
kubectl describe node k3d-ai-cluster-agent-0 | grep -A5 "nvidia.com/gpu"

# 关键 workload 状态
kubectl get pods -n ai-platform        # vllm-3b / vllm / ollama 应 Running
kubectl get pods -n cyberrouter        # operator
kubectl get pods -n gateway 2>/dev/null
kubectl get pods -n monitoring         # prometheus / gpu-exporter

# ArgoCD 同步状态（走 GitOps 时）
kubectl get applications -n argocd
```

**两个已知坑（同步前先处理，否则会卡）**：
- `gpu-exporter` / `dcgm-exporter` 的 `nodeSelector: node-role: gpu`，与 GPU 节点实际标签 `nvidia.com/gpu=true` **不一致** → 部署前统一标签约定（`kubectl label node k3d-ai-cluster-agent-0 node-role=gpu --overwrite`，rebuild.sh 已做）。
- 单 GPU 滚动更新死锁：`replicas=1` + RollingUpdate → 先建新 pod 占不到 GPU → 永远 Pending。`vllm-3b` 已改 **Recreate**；手动操作等价格 = `kubectl delete pod` 强制重建。

---

## 4. 端到端验证（e2e 清单）

> 顺序：**先通小后通大**。每步有明确的"通过"判定，不复现就不算过。

### 4.1 网关连通性（数据面）

```bash
# gateway 在 CPU 节点 (agent-1)，先确认 pod 就绪
kubectl -n ai-platform get pod -l app=ai-infra-gateway

# 端口转发（或走 ingress http://localhost/gateway/）
kubectl -n ai-platform port-forward svc/ai-infra-gateway 8000:8000 &

curl -s http://localhost:8000/health | jq .          # → 200, {"status":"ok"}
curl -s http://localhost:8000/api/models | jq .       # → 200, 列 vllm-3b + ollama
```

**通过判定**：`/health` 200；`/api/models` 列出 `vllm-3b-service` 与 `ollama-service`。

### 4.2 vLLM 主服务 + LMCache（服务面）

```bash
kubectl -n ai-platform get pod -l app=vllm-3b
kubectl -n ai-platform port-forward svc/vllm-3b-service 8001:8000 &

curl -s http://localhost:8001/health          # 200
curl -s http://localhost:8001/v1/models | jq   # qwen2.5-3b-awq
```

**通过判定**：`/health` 200；`/v1/models` 返回模型名。

### 4.3 预判性降级闭环（控制面 → 数据面）

```bash
# 看快照（Operator 编译的 ConfigMap）含动态状态
kubectl get cm cyberrouter-routing-snapshot -o jsonpath='{.data.snapshot\.json}' | jq .tiers

# 打一个长请求，观察决策日志
curl -s http://localhost:8000/api/generate -H 'Content-Type: application/json' \
  -d '{"model":"vllm-3b-service","prompt":"<4000-token 长前缀>"}' | jq .decision
```

**通过判定**：快照 `.tiers[].status` 有 `healthy`/`running`/`cacheHitRatio`；长请求日志带 `Reasons` 推理链。

### 4.4 复现 benchmark 关键数字

| 数字 | 复现命令 | 通过判定 |
|---|---|---|
| **vLLM 前缀缓存命中率 95.1%** | `heteroserve/benchmark/` 共享前缀负载（A1：39312/41350） | vLLM `/metrics` 里 `vllm:prefix_cache_hits_total / vllm:prefix_cache_queries_total` ≥ 0.95 |
| **TTFT 3-5×（LMCache 分层恢复）** | `benchmark.py` before/after（20 个互异 4000-token 前缀，> GPU KV 65K 触发逐出） | 逐出前缀恢复 0.60s → 0.11–0.19s |
| **熔断器 503** | 停掉 ollama，连打 > 阈值次 `/api/generate` | 熔断 OPEN 后返回 503 "熔断: ollama 持续不可达" |
| **401 计数** | 无 token 请求 `/api/models` | Prometheus `request_count{status_code="401"}` 递增 |

**vLLM 指标确认**：
```bash
kubectl -n ai-platform port-forward svc/vllm-3b-service 8001:8000 &
curl -s http://localhost:8001/metrics | grep -E "prefix_cache_(hits|queries)_total|num_requests_running"
```

---

## 5. 坑清单速查（环境级）

| 坑 | 症状 | 处理 |
|---|---|---|
| 驱动目录改名 | 节点 exit 127 | §2 路径 A sed CDI（或 `nvidia-ctk cdi generate`） |
| CDI spec 过期 | `docker run --gpus all` 不可靠 | 手动挂载配方（environment.md §3.1）兜底 |
| 模型不在节点 | hostPath 空 | `docker cp` 进 agent-0，注意 models/models 嵌套 |
| k3d image import bug | "content digest not found" | docker save + ctr import |
| GPU 资源死锁 | Error pod 占 nvidia.com/gpu | 先删 Error pod 再建新 |
| exporter 标签不匹配 | GPU 指标没数据 | 统一 `node-role: gpu` 或改 nodeSelector |
| 滚动更新死锁 | 新 pod 永远 Pending | vLLM-3B 用 Recreate；手动 = delete pod |
| port-forward 未就绪 | 首个请求 refused | 先轮询 `/v1/models` 通再发压测（内置 30s 探测） |

---

## 6. GitOps 纪律

- 本文档与任何对 `workspace/working-platform/*` 清单、`/etc/cdi` 无关——**CDI 是宿主机系统文件，不进 git**（记入 environment.md 即可）。
- 若修复过程中改了 manifests / 脚本（如 gateway tag、标签约定），**commit + push**，ArgoCD 才能同步。
- 修复完成、e2e 全绿后：更新 `P1_验证发现.md` 待办区（"集群 e2e" 置 ✅），并在 `秋招准备/02_项目一` §四 #7 勾掉。
