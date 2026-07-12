# Integration Guide — AI Model Scheduler with AI Infra Gateway & SRE-LAB

This guide explains how to connect the **AI Model Scheduler** with the two existing projects to form a unified inference system.

---

## Quick Integration Map

```
┌─────────────────────────────────────────────────────────┐
│                   AI Model Scheduler                     │
│                   http://localhost:9000                  │
│                                                         │
│  Backend Pool:                                          │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────┐ │
│  │ Backend A    │  │ Backend B     │  │ Backend C      │ │
│  │ Ollama A     │  │ Ollama B      │  │ Mock/Real vLLM │ │
│  │ :11434       │  │ :11435        │  │ :11436         │ │
│  └──────┬──────┘  └──────┬───────┘  └───────┬────────┘ │
└─────────┼────────────────┼──────────────────┼──────────┘
          │                │                  │
┌─────────▼────────────────▼──────────────────▼──────────┐
│                     Inference Backends                   │
│                                                         │
│  :11434 ← ai-infra-gateway Ollama (RTX 4060 Laptop)    │
│  :11435 ← ai-infra-gateway Ollama Instance B            │
│  :11436 ← Mock vLLM (or real AutoDL RTX 4090)           │
│  (future) ← SRE-LAB K3S Ollama StatefulSet Service      │
└─────────────────────────────────────────────────────────┘
```

---

## 1. Integration with AI Infra Gateway

### Step 1: Verify Gateway is Running

```powershell
# AI Infra Gateway's Ollama should be on port 11434
curl http://localhost:11434/api/tags
# → {"models": [{"name": "qwen2.5:0.5b"}, {"name": "qwen2.5:1.5b"}]}
```

### Step 2: Start Second Ollama Instance (Optional)

For a richer demo with two distinct local backends:

```powershell
# Terminal: Ollama B on port 11435
$env:OLLAMA_HOST="127.0.0.1:11435"
& "C:\Users\admin\AppData\Local\Programs\Ollama\ollama.exe" serve
```

### Step 3: Verify Scheduler Config

`01-scheduler-core/config/scheduler_config.yaml` should already reference the correct ports:

```yaml
backends:
  - id: "ollama-local-a"
    url: "http://127.0.0.1:11434"   # AI Infra Gateway's Ollama
    models:
      - "qwen2.5:0.5b"

  - id: "ollama-local-b"
    url: "http://127.0.0.1:11435"   # Second Ollama instance
    models:
      - "qwen2.5:1.5b"
```

### Step 4: What the Scheduler Adds

AI Infra Gateway already has JWT auth, rate limiting, and circuit breaker. The Scheduler **adds above that**:

- **Multi-backend routing**: Instead of a single Ollama, the Gateway now has 2+ backends to choose from
- **Strategy-based selection**: Latency-priority / cost-priority / throughput-priority
- **Session affinity**: KV Cache reuse across requests
- **Unified metrics**: Single `/v1/status` endpoint shows all backends

---

## 2. Integration with SRE-LAB (K3S Cluster)

### Architecture

```
Scheduler :9000
     │
     ├──→ Ollama Local A (bare metal :11434)
     ├──→ Ollama Local B (bare metal :11435)
     ├──→ Mock vLLM (:11436)
     └──→ K3S Ollama Service (future)
              │
              └── Ollama StatefulSet (K3S cluster)
                   · Ingress: traefik → ollama-service:11434
                   · HPA: auto-scale based on load
                   · PVC: persistent model storage
```

### Step 1: Expose K3S Ollama Service

If K3S is on a different machine, expose the service:

```bash
# On K3S node — port-forward for testing
kubectl port-forward -n ai-platform svc/ollama-service 11437:11434

# Or create a NodePort service for permanent access
kubectl expose deployment ollama -n ai-platform --type=NodePort --port=11434
```

### Step 2: Register K3S Backend in Scheduler

Add to `scheduler_config.yaml`:

```yaml
backends:
  # ... existing backends ...

  - id: "k3s-ollama"
    name: "K3S Ollama (Cluster)"
    url: "http://<K3S_NODE_IP>:11437"   # or NodePort
    api_path: "/api/generate"
    chat_path: "/api/chat"
    health_path: "/api/tags"
    models:
      - "qwen2.5:1.5b"
      - "qwen2.5:7b"       # larger model on K3S GPU node
    engine: "ollama"
    cost_per_token: 0.0
    max_concurrency: 16
    weight: 1.5
    tags:
      - "cluster"
      - "scalable"
      - "production"
```

### Step 3: HPA Benefits with Scheduler

When K3S HPA scales up:
- New Ollama Pods come online → Scheduler health check detects them → auto-registers as additional capacity
- Active requests distribute across more Pods via weighted routing

When HPA scales down:
- Terminated Pods fail health check → marked UNHEALTHY → traffic stops

---

## 3. Adding a Real vLLM Cloud Backend

### Step 1: Deploy vLLM on AutoDL (or any cloud GPU)

Follow the existing scripts in `ai-infra-gateway/05-autodl-benchmark/`:

```bash
# SSH into AutoDL RTX 4090 instance
bash vllm_deploy.sh
python -m vllm.entrypoints.openai.api_server \
  --model qwen/Qwen2.5-0.5B-Instruct \
  --port 8000
```

### Step 2: Register in Scheduler Config

```yaml
backends:
  - id: "autodl-vllm"
    name: "AutoDL vLLM (RTX 4090)"
    url: "http://<AUTODL_IP>:8000"
    api_path: "/v1/completions"
    chat_path: "/v1/chat/completions"
    health_path: "/health"
    models:
      - "qwen2.5:0.5b"
      - "qwen2.5:1.5b"
      - "qwen2.5:7b"
    engine: "vllm"
    cost_per_token: 0.000005    # actual cost from AutoDL pricing
    max_concurrency: 64
    weight: 3.0                 # prefer vLLM for performance
    tags:
      - "cloud"
      - "high-performance"
      - "vllm"
```

### Step 3: Verify

```bash
# Check scheduler sees all backends
curl http://localhost:9000/v1/backends

# Route with cost strategy → should pick local (free)
curl -X POST http://localhost:9000/v1/chat/completions \
  -H "Authorization: Bearer test-key" \
  -H "X-Routing-Strategy: cost" \
  ...

# Route with latency strategy → should pick vLLM (low TTFT)
curl -X POST http://localhost:9000/v1/chat/completions \
  -H "Authorization: Bearer test-key" \
  -H "X-Routing-Strategy: latency" \
  ...
```

---

## 4. Cloud GPU Verification（是否需要上云？）

### 现状评估

**Scheduler 本身是纯 CPU + 网络 I/O 的软件层**，不执行任何 GPU 推理。其核心功能（路由决策、健康检查、限流、熔断）100% 可在本地完成开发和验证。

| 验证目标 | 本地可行？ | 说明 |
|---------|-----------|------|
| 路由策略正确性 | ✅ | Mock 后端 + 2个真实 Ollama = 3个异构后端 |
| 健康检查 + 熔断降级 | ✅ | 手动停止一个后端验证自动摘除 |
| 并发压测 C1→C16 | ✅ | 本地 asyncio 并发即可 |
| 延迟/成本/吞吐路由策略对比 | ✅ | Mock vLLM (TTFT 50ms) vs 真实 Ollama (TTFT 3s) |
| Session 亲和性验证 | ✅ | 发送带 X-Session-Id 的多次请求 |
| **真实 vLLM 端到端压测** | ⚠️ 需要云GPU | 仅需 2-4 小时，约 ¥5-10 |
| **最终多后端对比报告** | ⚠️ 建议但非必须 | Mock 数据 + AI Infra Gateway 已有 vLLM 数据可引用 |

### 推荐方案：本地 90% + 云验证 10%

1. **本地完成**：全部代码开发、单元测试、Mock 后端压测、Dashboard 调通
2. **云验证 2-4 小时**（可选，非必须）：
   - 租用 AutoDL RTX 4090 最低配置（约 ¥2/小时）
   - 复用 `ai-infra-gateway/05-autodl-benchmark/` 的 vLLM 部署脚本
   - 将 vLLM 后端注册到 Scheduler
   - 运行 `scheduler_stress_test.py` 和 `cross_backend_compare.py`
   - 截图保存 Dashboard 显示 4 个后端（Ollama A/B + Mock vLLM + Real vLLM）的对比数据

### 如果不上云如何展示同样效果？

Scheduler 项目的 Mock vLLM 后端 (`mock_backend.py`) 已经模拟了 vLLM 的核心特征：
- TTFT 30-80ms（真实 vLLM ~32ms，本地 Ollama ~3400ms）
- TPOT 5ms/字符（足够精细的 token 级流式输出）
- 多模型支持（同时注册 0.5B + 1.5B）

**本地 3 后端（Ollama A + Ollama B + Mock vLLM）已经能够完整展示**：
- 延迟路由：自动选 Mock vLLM（TTFT 最低）
- 成本路由：自动选 Ollama 本地（cost=0）
- 吞吐路由：自动选 Mock vLLM（吞吐最高）

这已经构成了一个完整的异构调度演示，云 GPU 仅作为"锦上添花"的可选项。

---

## 5. End-to-End Demo Script

```powershell
# === Terminal 1: Start Ollama A ===
ollama serve
# (default :11434)

# === Terminal 2: Start Ollama B ===
$env:OLLAMA_HOST="127.0.0.1:11435"
& "C:\Users\admin\AppData\Local\Programs\Ollama\ollama.exe" serve

# === Terminal 3: Start Mock vLLM ===
cd aimodel-scheduler/01-scheduler-core
python mock_backend.py
# → :11436

# === Terminal 4: Start Scheduler ===
cd aimodel-scheduler/02-unified-api
python unified_gateway.py
# → :9000

# === Terminal 5: Dashboard ===
cd aimodel-scheduler/04-observability
python dashboard.py
# → http://localhost:9010

# === Terminal 6: Run Benchmark ===
cd aimodel-scheduler/03-scheduler-benchmark
python scheduler_stress_test.py
```

**Expected output**: Scheduler distributes requests across all 3 backends, Dashboard shows live metrics, Benchmark prints concurrency gradient results.

---

## 6. Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `No healthy backend available` | Ollama not running | Start `ollama serve` |
| `Circuit breaker OPEN` | Backend returning errors | Check backend logs; restart if needed |
| `Rate limit exceeded` | Too many concurrent requests | Increase `rate_limit.global.capacity` in config |
| Scheduler can't import modules | Wrong working directory | Run from `02-unified-api/` directory |
| Dashboard shows no data | Scheduler not running on :9000 | Start `unified_gateway.py` first |