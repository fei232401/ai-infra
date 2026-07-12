# AI Model Scheduler — Architecture Design

## 1. Overview

The **AI Model Scheduler** is the third component in the AI Infra system, sitting on top of **AI Infra Gateway** (Windows bare metal Ollama) and **SRE-LAB** (K3S cloud-native platform). It provides:

- **Unified Entry Point**: Single API endpoint for all inference requests
- **Intelligent Routing**: Model-aware, latency-priority, throughput-priority, cost-aware
- **Session Affinity**: Sticky routing for KV Cache reuse
- **Circuit Breaker**: Automatic isolation of failing backends
- **Adaptive Scoring**: Rolling-window performance tracking per backend

---

## 2. Component Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                  02-unified-api (FastAPI)                    │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────────┐  │
│  │ Auth        │  │ Rate Limit   │  │ SSE Proxy         │  │
│  │ Bearer Token│  │ Token Bucket │  │ Streaming Forward │  │
│  └─────────────┘  └──────────────┘  └───────────────────┘  │
├─────────────────────────────────────────────────────────────┤
│                  01-scheduler-core                           │
│  ┌──────────────────────────────────────────────────────┐   │
│  │                   Scheduler                            │   │
│  │  route_request() → record_result() → get_status()    │   │
│  └──────┬───────────────────────────────┬───────────────┘   │
│         │                               │                    │
│  ┌──────▼──────────┐          ┌─────────▼──────────────┐   │
│  │  Routing Engine  │          │   Backend Registry      │   │
│  │  · model_aware   │          │   · Health Check Loop   │   │
│  │  · latency       │          │   · Circuit Breaker     │   │
│  │  · throughput    │          │   · Score Tracking      │   │
│  │  · cost          │          │   · Backend CRUD        │   │
│  │  · affinity      │          └────────────────────────┘   │
│  └──────────────────┘                                       │
│         │                                                   │
│  ┌──────▼──────────┐                                        │
│  │  Load Balancer   │                                       │
│  │  · Least Conn    │                                       │
│  │  · Adaptive Score│                                       │
│  │  · Weighted RR   │                                       │
│  └──────────────────┘                                       │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. Routing Decision Flow

```
Request enters /v1/chat/completions
         │
         ▼
    1. Auth Middleware (Bearer Token validation)
         │
         ▼
    2. Parse body → extract model, stream, messages
         │
         ▼
    3. Scheduler.route_request(model, strategy, session_id)
         │
         ├── 3a. Model-Aware Filter: only backends with this model
         ├── 3b. Health Filter: only HEALTHY + CIRCUIT_CLOSED
         ├── 3c. Concurrency Filter: active < max_concurrency
         ├── 3d. Affinity Check: session_id → cached backend
         └── 3e. Strategy Sort:
                · latency → lowest TTFT
                · throughput → highest TPS
                · cost → lowest cost_per_token
                · default → weighted random
         │
         ▼
    4. Per-backend Rate Limit Check
         │
         ▼
    5. Forward to backend URL + proxy response
         │
         ▼
    6. Record result → update backend score
```

---

## 4. Data Flow

### 4.1 Health Check Loop

```
Every 10s (configurable):
  For each backend:
    GET {backend.url}{backend.health_path}
    
    Success (HTTP < 500):
      → consecutive_successes++
      → if >= recovery_threshold → mark HEALTHY
      → if HALF_OPEN → CLOSED
    
    Failure (timeout / error / 5xx):
      → consecutive_failures++
      → if >= unhealthy_threshold → mark UNHEALTHY
      → if >= circuit_breaker threshold → OPEN
```

### 4.2 Score Tracking

```
Each request completion:
  record_result(backend_id, success, ttft_ms, tps, tokens)
  
  Success:
    → append ttft to rolling window (size=20)
    → append tps to rolling window
    → recalc: average_ttft, average_tps, error_rate
    
  Failure:
    → append error to rolling window
    → check circuit breaker threshold
```

---

## 5. Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Pure Python / FastAPI | Same stack as AI Infra Gateway; zero new dependencies |
| In-memory backend registry | No external DB needed for local dev; K8s Service Discovery for prod |
| Token Bucket rate limiting | Allows burst traffic; simple; same as Gateway v2.0 |
| Rolling window scoring | Adaptive to recent behavior; window_size=20 balances responsiveness |
| 3-state circuit breaker | Standard pattern (CLOSED → OPEN → HALF_OPEN); 3 failures trigger |
| YAML config | Human-readable; K8s ConfigMap compatible; no code change for new backends |

---

## 6. Integration Points

### 6.1 With AI Infra Gateway

Scheduler reuses the same FastAPI + aiohttp + YAML config patterns. The Gateway's Ollama :11434 becomes Backend A in the scheduler pool.

### 6.2 With SRE-LAB

Scheduler can discover K3S-internal services via Prometheus Service Discovery or static config. HPA scaling events are reflected in backend health checks (new Pods register, terminated Pods go unhealthy).

### 6.3 With Cloud GPU (vLLM)

The `mock_backend.py` simulates vLLM characteristics (TTFT ~50ms, high throughput). When a real vLLM instance is available (e.g., AutoDL RTX 4090), add it as a backend in `scheduler_config.yaml`.

---

## 7. Failure Modes & Recovery

| Failure | Detection | Recovery |
|---------|-----------|----------|
| Backend crash | Health check marks UNHEALTHY after 3 failures | Auto-recovery when health checks pass × 2 |
| Backend overload | Active requests = max_concurrency | Requests route to other backends |
| Backend slow | TTFT rises in scoring window | Latency-priority routing shifts traffic away |
| Circuit breaker OPEN | 3 consecutive failures | 30s timeout → HALF_OPEN → test with 2 requests |
| All backends down | `get_backends_for_model()` returns [] | Return HTTP 503 with detailed error |
| Scheduler crash | External health check (Prometheus `up` metric) | Restart process; in-memory state resets |