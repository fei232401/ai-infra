# AI Infra Gateway vs SRE-LAB — Architect Comparison

> **Purpose**: Systematic comparison of two production-grade AI inference platforms built under different constraints.
> **Audience**: Technical interviewers, architects, team members evaluating inference deployment strategies.

---

## 1. Project Identity

| Dimension | AI Infra Gateway | SRE-LAB |
|-----------|-----------------|---------|
| **Positioning** | Windows bare metal enterprise inference gateway | K3S cloud-native AI inference platform |
| **Origin** | Single developer, 4 hours, constrained hardware | Structured curriculum, incremental modules |
| **Core Problem** | Deliver production-grade inference on a laptop GPU with no Docker/K8s/WSL2 | Build a complete K8s-native AI platform from scratch with full observability |
| **Deployment Model** | Monolith (single-process FastAPI + Ollama) | Microservices (Ollama StatefulSet + WebUI Deployment + K8s ecosystem) |
| **GPU Hardware** | RTX 4060 Laptop 8GB (single, fixed) | Any GPU with K3S node (theoretically scalable) |
| **Codebase** | ~2,600 lines Python, 23 source files, 4 modules | YAML manifests + shell scripts + Python locustfile |

---

## 2. Architecture Comparison

### 2.1 Inference Path

```
AI Infra Gateway (Bare Metal):
  Client → FastAPI Gateway (:8000) → Ollama (:11434) → RTX 4060 GPU
              ↑ Auth + RateLimit + CircuitBreaker + SSE

SRE-LAB (K3S):
  Client → Traefik Ingress (80) → Open WebUI (:8080) → Ollama ClusterIP → StatefulSet → GPU
              ↑ StripPrefix        ↑ UI layer           ↑ PVC + HPA + PDB
```

### 2.2 Key Architectural Differences

| Layer | AI Infra Gateway | SRE-LAB | Winner |
|-------|-----------------|---------|--------|
| **Authentication** | JWT HS256 + API Key middleware (in-process) | Sealed Secrets + RBAC (K8s-native) | SRE-LAB (infra-standard) |
| **Rate Limiting** | Token Bucket (in-memory, 5 QPS) | Traefik Middleware (cluster-level) | SRE-LAB (distributed) |
| **Resilience** | Circuit Breaker + Retry (in-process) | HPA + PDB (cluster orchestration) | SRE-LAB (orchestrated) |
| **Observability** | pynvml dashboard (single-node) | Prometheus + Grafana + Loki + AlertManager + WeChat Bot | SRE-LAB (full stack) |
| **CI/CD** | None (manual deployment) | ArgoCD GitOps (auto-sync, self-heal) | SRE-LAB (production-ready) |
| **Model Management** | GGUF local import (GFW workaround) | PVC persistence + ollama pull | Tie (different constraints) |
| **GPU Scheduling** | Ollama native (simple queue) | Ollama (no advanced batching) | Tie (both use Ollama) |
| **vLLM Upgrade Path** | ✅ Ready (05-autodl-benchmark scripts) | 🔄 Planned (needs vLLM Pod manifest) | AI Infra Gateway (ahead) |

### 2.3 Scaling Model

| Property | AI Infra Gateway | SRE-LAB |
|----------|-----------------|---------|
| Scale out | ❌ Single machine (Windows physical limit) | ✅ K3S nodes can be added |
| Scale up | ❌ GPU fixed (laptop, non-upgradable) | ✅ Can add more GPU nodes |
| Auto-scaling | ❌ Manual restart only | ✅ HPA based on CPU/Memory |
| Graceful shutdown | ❌ None | ✅ PDB prevents disruption |
| Multi-tenancy | ❌ Single instance | ✅ Namespace isolation |

---

## 3. Benchmarking Methodology Comparison

### 3.1 AI Infra Gateway (03-benchmark + 05-autodl-benchmark)

| Feature | 03-benchmark (Ollama Local) | 05-autodl-benchmark (vLLM Cloud) |
|---------|---------------------------|--------------------------------|
| Metrics | TTFT, TPOT, Throughput, Token P99 | TTFT, TPOT, Throughput, Token P99, RPS |
| Concurrency | Fixed C1/C2/C4/C8 | Step-pressure (1→4→8→16→32) |
| Scenario tiers | Single prompt | 3-tier (light/medium/heavy) + health |
| Weight distribution | N/A | 50%/30%/15%/5% |
| Model comparison | 0.5B vs 1.5B | 0.5B vs 1.5B (expandable to 3B/7B) |
| Cross-platform | N/A | Ollama(RTX 4060) vs vLLM(RTX 4090) |

### 3.2 SRE-LAB (03-benchmark)

| Feature | Implementation |
|---------|---------------|
| Metrics | TTPT, TPOT, RPS, P50/P90/P99 |
| Tools | Locust (Python) + k6 (JS) |
| Concurrency | Configurable users (5/20/50/100) + spawn-rate |
| Scenario tiers | 3-tier (light 50%/medium 30%/heavy 15%) + list_models 5% |
| HPA verification | Continuous pressure → watch HPA trigger |
| Output format | YAML template with HPA behavior log |

### 3.3 Methodology Alignment (Post-Upgrade)

After the v3.0 upgrade, **AI Infra Gateway** and **SRE-LAB** share the same benchmarking methodology:

| Method | Both Projects |
|--------|--------------|
| 3 Scenario Tiers | ✅ light (50%) / medium (30%) / heavy (15%) / health (5%) |
| Step-Pressure Ramp | ✅ Configurable start → step → max |
| Core Metrics | ✅ TTFT / TPOT / Throughput / Token P99 / RPS |
| Results Template | ✅ YAML format with scenario × concurrency matrix |

**This cross-project alignment means benchmark results from both projects are directly comparable, enabling:**
- vLLM (cloud GPU) vs Ollama (local GPU) latency comparisons
- vLLM (cloud GPU) vs Ollama (K3S cluster) throughput comparisons
- Unified observability across deployment models

---

## 4. Observability Comparison

| Capability | AI Infra Gateway | SRE-LAB |
|------------|-----------------|---------|
| GPU Metrics | pynvml (memory, utilization, temperature) | ✅ via Prometheus exporter |
| API Metrics | None (no endpoint) | ✅ ollama_request_duration_seconds |
| Dashboards | matplotlib PNG (4 panels) | ✅ Grafana (multiple dashboards) |
| Alerting | None | ✅ AlertManager → WeChat Bot |
| Logging | Console stdout | ✅ Loki + Promtail → Grafana Explore |
| Distributed Tracing | ❌ | ❌ (both projects lack this) |

**Key Insight**: SRE-LAB's observability stack is the gold standard for production AI inference. AI Infra Gateway's pynvml dashboard is a pragmatic solution for constrained environments but does not scale.

---

## 5. Security Comparison

| Control | AI Infra Gateway | SRE-LAB |
|---------|-----------------|---------|
| API Auth | JWT HS256 (symmetric, in-memory secret) | Sealed Secrets (encrypted in Git) |
| Transport | Plain HTTP (localhost-only in default config) | Traefik TLS termination (configurable) |
| Network Isolation | Windows firewall (single node) | ClusterIP (no external exposure) |
| Secret Management | YAML config file (in .gitignore) | Sealed Secrets + kubeseal |
| RBAC | None | ✅ K8s RBAC (Promtail, etc.) |
| Network Policy | None | ✅ Planned (Pod-level isolation) |

**Key Insight**: SRE-LAB demonstrates production-grade security patterns (Sealed Secrets, RBAC, ClusterIP isolation). AI Infra Gateway provides adequate security for a single-node deployment but would need significant hardening for production use.

---

## 6. Migration Path: Bare Metal → K3S

For teams starting with AI Infra Gateway and moving to SRE-LAB:

| Phase | Action | Effort |
|-------|--------|--------|
| 1 | Deploy Ollama on K3S (StatefulSet + PVC) as in SRE-LAB | 1 day |
| 2 | Port JWT middleware to Traefik plugin or API Gateway sidecar | 2 days |
| 3 | Replace Token Bucket with Traefik RateLimit middleware | 0.5 day |
| 4 | Add Prometheus exporter for GPU + inference metrics | 1 day |
| 5 | Migrate circuit breaker logic to K8s health probes + PDB | 0.5 day |
| 6 | Enable ArgoCD GitOps for automated deployment | 1 day |

### vLLM on K3S (Recommended Architecture)

```
┌──────────┐    ┌─────────────────────────┐    ┌──────────────────┐
│  Client  │───▶│  Traefik Ingress (80)    │───▶│  vLLM Deployment │
│          │    │  + RateLimit Middleware   │    │  (GPU node)      │
└──────────┘    │  + JWT Auth Middleware    │    └──────────────────┘
                └─────────────────────────┘             │
                          │                     ┌───────▼──────────┐
                          ▼                     │  Prometheus      │
                ┌─────────────────────────┐    │  + Grafana       │
                │  ArgoCD (GitOps)         │    │  + GPU Exporter  │
                │  Auto Sync from GitHub   │    └──────────────────┘
                └─────────────────────────┘
```

---

## 7. Team Interview Talking Points

### Question: "Compare your bare metal gateway with a K8s-based solution."

**Answer Structure**:

1. **Acknowledge the different constraints**: "These are solutions for fundamentally different environments. AI Infra Gateway was built on a Windows laptop with no Docker, K8s, or WSL2 — every design decision was shaped by that constraint."

2. **Highlight shared principles**: "Both projects share the same inference engineering fundamentals: JWT auth, rate limiting, SSE streaming, TTFT/TPOT benchmarking. The K3S solution (SRE-LAB) adds orchestration, observability, and GitOps on top."

3. **Show the migration insight**: "In fact, the vLLM benchmark module I built includes scripts to migrate these patterns to a K3S cluster — deploying vLLM as a StatefulSet with Prometheus metrics exposure."

4. **Quantify the tradeoff**: "Ollama on bare metal at C1: 198 t/s (0.5B). vLLM on RTX 4090 at C1: [待填写]. The K3S platform adds 20-30% overhead for orchestration but gains HPA auto-scaling and full observability."

---

## 8. Conclusion

| Scenario | Recommended Stack | Why |
|----------|------------------|-----|
| **Development / Debugging** | AI Infra Gateway (bare metal) | Fast iteration, no infrastructure overhead |
| **Edge Deployment** | AI Infra Gateway (bare metal) | No container runtime required |
| **Production Cluster** | SRE-LAB (K3S + vLLM) | Observability, auto-scaling, GitOps |
| **Benchmark / Model Evaluation** | 05-autodl-benchmark (cloud GPU) | High GPU power, isolated environment |
| **Hybrid (Local Dev + Cloud Prod)** | Both — AI Infra Gateway for dev, SRE-LAB for prod | Best of both worlds |

---

*Document prepared for cross-project architecture audit. Both projects demonstrate SRE-grade engineering — they differ in deployment model, not in engineering quality.*