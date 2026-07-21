# AI Infra Gateway

<div align="center">

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688?logo=fastapi)](https://fastapi.tiangolo.com)
[![Ollama](https://img.shields.io/badge/Ollama-0.30%2B-white?logo=ollama)](https://ollama.com)
[![Prometheus](https://img.shields.io/badge/Prometheus-integrated-E6522C?logo=prometheus)](https://prometheus.io)
[![License](https://img.shields.io/badge/license-MIT-green)](./LICENSE)
[![Status](https://img.shields.io/badge/status-production%20ready-brightgreen)]()
[![Platform](https://img.shields.io/badge/platform-Windows%2011-0078D6?logo=windows)]()

**Enterprise LLM Inference Gateway on Windows Bare Metal — No Docker, No K8s, Just GPU**

[Quick Start](#-quick-start) · [Architecture](#-architecture) · [Benchmark](#-benchmark-results) · [Prometheus Integration](#-prometheus--grafana-集成) · [Scheduler Integration](#-与-ai-model-scheduler-对接) · [Troubleshooting](docs/troubleshooting.md) · [Changelog](CHANGELOG.md) · [Full Story](docs/PROJECT_NARRATIVE.md)

</div>

---

## Table of Contents

1. [Overview](#-overview)
2. [Architecture](#-architecture)
3. [Quick Start](#-quick-start)
4. [Benchmark Results](#-benchmark-results)
5. [Prometheus & Grafana 集成](#-prometheus--grafana-集成)
6. [与 AI Model Scheduler 对接](#-与-ai-model-scheduler-对接)
7. [vLLM Cloud GPU Benchmark](#-vllm-cloud-gpu-benchmark)
8. [Troubleshooting Highlights](#-troubleshooting-highlights)
9. [Tech Stack](#-tech-stack)
10. [Roadmap](#-roadmap)
11. [Document Index](#-document-index)

---

## 🎯 Overview

A production-grade LLM inference API gateway built entirely on **Windows 11 bare metal** with a single **NVIDIA RTX 4060 Laptop GPU (8GB)** — no Docker, no Kubernetes, no WSL2.

| Key Capability | Implementation |
|---------------|----------------|
| Authentication | JWT (HS256) + API Key fallback |
| Rate Limiting | Token Bucket (5 QPS, burst=10) |
| Resilience | Circuit Breaker (3-failure → OPEN) + Retry (2x, 1s backoff) |
| Streaming | SSE proxy — per-token forwarding to client |
| Monitoring | Real-time GPU dashboard (pynvml + matplotlib, 4 panels, 3s refresh) |
| Prometheus Metrics | metrics_exporter.py → :9090, GPU + Ollama 指标 |
| Benchmarking | Dual-model C1-C8 concurrency gradient with TTFT/TPOT/Throughput/P99 |
| Scheduler Integration | 注册为 AI Model Scheduler 的推理后端 |

> For the full environment baseline, project backstory, and deep-dive into every design decision, see **[PROJECT_NARRATIVE.md](docs/PROJECT_NARRATIVE.md)**.

---

## 🏗️ Architecture

```
                    ┌─────────────────┐
                    │   Dashboard v2  │  :9090  GPU real-time monitor
                    │  (FastAPI+matp) │
                    └────────┬────────┘
                             │ pynvml (NVIDIA driver-level sampling)
┌──────────┐    ┌───────────▼──────────────┐    ┌──────────┐
│  Client  │───▶│   Inference Gateway :8000 │───▶│  Ollama  │
│  (HTTP)  │    │  Auth + RateLimit + SSE   │    │  :11434  │
└──────────┘    └──────────────────────────┘    └──────────┘
     │                │              │                │
     ▼                ▼              ▼                ▼
 Bearer Token     Token Bucket    SSE Stream      RTX 4060
 JWT / API Key    (5 QPS, b=10)   per-token       8GB VRAM
```

---

## 🚀 Quick Start

```powershell
# 1. Start Ollama inference engine
C:\Users\admin\AppData\Local\Programs\Ollama\ollama.exe serve

# 2. Start API Gateway
cd 01-gateway-server
python start_gateway.py                    # → http://localhost:8000

# 3. Start GPU dashboard
cd 02-dashboard
python dashboard_v2.py                     # → http://localhost:9090

# 4. Start Prometheus Exporter
cd 04-infrastructure
python metrics_exporter.py                 # → http://localhost:9090/metrics

# 5. Run full benchmark
cd 03-benchmark
python benchmark_final.py
```

---

## 📊 Benchmark Results

### Ollama Local Baseline (RTX 4060 Laptop 8GB)

| Metric | qwen2.5:0.5b (397 MB) | qwen2.5:1.5b (986 MB) |
|--------|----------------------|----------------------|
| C1 Throughput | **198 t/s** | **132 t/s** |
| C1 TTFT | 3,383ms | 6,412ms |
| TPOT (avg) | 8ms | 14–15ms |
| Success rate | 100% (32/32) | 100% (32/32) |

### vLLM Cloud GPU (RTX 4090 24GB)

| Metric | qwen2.5:0.5b (measured) | qwen2.5:1.5b (extrapolated) |
|--------|------------------------|----------------------------|
| C1 Throughput | **5,132 t/s** | **1,711 t/s** |
| C4 Throughput | **17,778 t/s** | **5,926 t/s** |
| C1 TTFT | 32ms | 68ms |
| TPOT (avg) | 2ms | 6–7ms |
| GPU | RTX 4090 24GB | RTX 4090 24GB |
| Engine | vLLM 0.23.0 | vLLM 0.23.0 |

---

## 📈 Prometheus & Grafana 集成

`04-infrastructure/metrics_exporter.py` 在 **:9090** 端口暴露标准指标：

```python
ai_infra_gpu_memory_used_mb        # GPU 显存已用量
ai_infra_gpu_memory_free_mb        # GPU 显存空闲量
ai_infra_gpu_utilization_pct       # GPU 利用率
ai_infra_gpu_temperature_c         # GPU 温度
ai_infra_ollama_models_loaded      # 已加载模型数
ai_infra_ollama_service_up         # Ollama 服务是否可达
ai_infra_inference_ttft_ms         # 首字延迟
ai_infra_inference_throughput_tps  # 推理吞吐
```

对接 ECS K3S Prometheus：
```yaml
scrape_configs:
  - job_name: 'ai-infra-gateway'
    static_configs:
      - targets: ['<本机IP>:9090']
```

---

## 🔗 与 AI Model Scheduler 对接

本项目的 Ollama 实例注册为 AI Model Scheduler 的推理后端：

```yaml
# 在 Scheduler 的 scheduler_config.yaml 中添加
backends:
  - id: "ollama-local-a"
    name: "AI Infra Gateway Ollama"
    url: "http://127.0.0.1:11434"
    engine: "ollama"
    models:
      - "qwen2.5:0.5b"
      - "qwen2.5:1.5b"
```

```
Client → AI Model Scheduler :9000 → AI Infra Gateway :11434 (Ollama)
                                      · 路由策略控制
                                      · 健康检查/断路器保护/限流
```

---

## 🚀 vLLM Cloud GPU Benchmark

```bash
# 在 AutoDL RTX 4090 实例上
bash vllm_deploy.sh     # 安装 vLLM + 下载模型 + 启动 API
python vllm_benchmark.py  # 阶梯并发压测 (1→4→8→16→32)
```

压测方法已与 SRE-LAB 对齐：3 级场景（light/medium/heavy）+ 阶梯并发。

---

## 🔥 Troubleshooting Highlights

| ID | Priority | Issue | Status |
|----|----------|-------|--------|
| T-001 | P0 | Ollama Registry blocked by GFW → GGUF local import bypass | ✅ Resolved |
| T-002 | P0 | Gateway 8000 port not listening after startup | ✅ Resolved |
| T-003 | P1 | PowerShell terminal stdout swallowed by IDE | 🟡 Workaround |
| T-004 | P2 | requirements.txt GBK encoding error on Windows pip | ✅ Resolved |
| T-005 | P1 | WSL2 / Hyper-V unavailable — OEM BIOS VT-x flag bug | 🔴 Hardware limit |

---

## 🛠️ Tech Stack

| Concern | Choice |
|---------|--------|
| Inference | Ollama 0.30.9 |
| API Server | FastAPI + aiohttp |
| Auth | PyJWT (HS256) |
| Rate Limiting | Token Bucket (in-memory) |
| Streaming | SSE (Server-Sent Events) |
| GPU Monitoring | pynvml 11.0+ |
| **Prometheus Export** | **metrics_exporter.py** |
| **Scheduler Integration** | **scheduler_config.yaml** |

---

*Built with FastAPI + Ollama + pynvml on Windows 11 · RTX 4060 Laptop GPU · v3.0.0*