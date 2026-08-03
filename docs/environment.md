# Phase 0 环境交接文档 — 2026-08-04

> **用途**：明天或任何时候回来，读这一份 + `Blueprint.md`（蓝图）就能接上，无需重新喂上下文。
> **配套**：项目蓝图在 `Blueprint.md`（当前在 /home/fei/projects/Blueprint.md，建议移入本仓库 docs/）。
> **本文件状态**：随环境变化持续更新。

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
| vLLM 7B-AWQ | ai-platform (`vllm-service:8000`) | ✅ 推理通过 |
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

---

## 五、GitOps 结构与目录

```
project/ (git 仓库 ai-infra, ArgoCD 绑定)
├── docs/                    ← 本文档 + 蓝图（建议移入）
├── k3d/                     ← 集群配置 (registries.yaml + manifests/)
│   └── manifests/
│       ├── nvidia-device-plugin.yaml
│       └── gpu-test-pod.yaml
├── ai-infra-gateway/        ← 数据面 gateway 代码 (Phase A 基线)
│   └── 01-gateway-server/
├── workspace/working-platform/  ← ArgoCD app-of-apps
│   ├── bootstrap/           ← root-app + 各 Application
│   ├── gateway/             ← gateway manifests
│   ├── monitoring-stack/    ← kube-prometheus-stack values
│   └── apps/                ← 各应用 (ai-platform, loki, promtail, exporters)
```

**ArgoCD 现状**：仓库里定义了 app-of-apps（root-app 自愈+prune），但**新集群还没部署 ArgoCD**。目前处于"manifests 进 git + kubectl 手动应用"模式——这对开发期是最舒服的。何时启用 ArgoCD 见《目录结构与 GitOps 评估》部分。

**提交纪律**：所有 manifest 变更 → git add/commit/push（防止未来 ArgoCD sync 时回滚/漂移）。

---

## 六、下一步（Phase 1：HeteroServe v3 骨架）

详见 `Blueprint.md` 第七节。核心：

1. **vLLM 调参矩阵**（H2）：`gpu_memory_utilization` 0.7/0.8/0.9 × `max_num_seqs` 8/16/32，记录真实 throughput/latency。
2. **LMCache 接入**（H1）：重点研究 `study/LMCache` 源码，分层缓存 GPU/CPU/SSD。
3. **前缀复用 benchmark**（H3）：固定 5K 共享前缀 + 变化尾巴，对比缓存前后 TTFT/命中率。
4. 缓存指标暴露给 Prometheus（H4），为 Phase 2 调度器联动做准备。

**深挖区学习顺序**（Blueprint §6.1）：vLLM KV 机制 → LMCache 集成点 → 排队模型 → Operator。

---

## 修订记录

| 版本 | 日期 | 内容 |
|---|---|---|
| v1.0 | 2026-08-04 | Phase 0 完成：集群+GPU+监控+双后端，全部配方与踩坑 |
