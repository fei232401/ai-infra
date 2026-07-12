# Routing Strategies — Detailed Reference

This document explains each routing strategy available in the AI Model Scheduler.

---

## Strategy Overview

| Strategy | Header Value | Selection Logic | Best For |
|----------|-------------|-----------------|----------|
| **Model-Aware** | *(default)* | Weighted random among backends with the model | General use |
| **Latency-Priority** | `latency` | Lowest historical TTFT | Real-time chat |
| **Throughput-Priority** | `throughput` | Highest historical TPS | Batch processing |
| **Cost-Aware** | `cost` | Lowest `cost_per_token` | Budget-constrained |
| **Session Affinity** | `X-Session-Id` header | Sticky to same backend | KV Cache reuse |

---

## 1. Model-Aware (Default)

**How it works**: Filters backends to only those serving the requested model, then distributes via weighted random selection.

**Weight**: Each backend has a `weight` in config. Higher = more traffic.
```yaml
backends:
  - id: "ollama-local-a"
    weight: 1.0    # receives ~33% of traffic (when 3 backends)
  - id: "mock-vllm"
    weight: 2.0    # receives ~67% of traffic
```

**Use case**: Day-to-day inference where you want balanced distribution.

---

## 2. Latency-Priority

**How it works**: Selects the backend with the **lowest average TTFT** in its rolling window (last 20 requests).

**Header**: `X-Routing-Strategy: latency`

**Example Scores**:
```
ollama-local-a:  TTFT=3412ms  ← not selected (too slow)
mock-vllm:       TTFT=55ms    ← SELECTED (fastest)
ollama-local-b:  TTFT=3897ms  ← not selected
```

**Use case**: Interactive chat applications where every millisecond matters.

**Caveat**: Cold backends with 0 history get `ttft=0` initially → will be preferred. Send a warm-up request first.

---

## 3. Throughput-Priority

**How it works**: Selects the backend with the **highest average throughput** (tokens/second) in its rolling window.

**Header**: `X-Routing-Strategy: throughput`

**Example Scores**:
```
ollama-local-a:  TPS=198   ← not selected
mock-vllm:       TPS=5132  ← SELECTED (highest TPS)
ollama-local-b:  TPS=132   ← not selected
```

**Use case**: Bulk/batch inference, document summarization, large context processing.

---

## 4. Cost-Aware

**How it works**: Selects the backend with the **lowest `cost_per_token`** (from config YAML).

**Header**: `X-Routing-Strategy: cost`

**Example Config**:
```yaml
backends:
  - id: "ollama-local-a"
    cost_per_token: 0.0          # ← SELECTED (free!)
  - id: "mock-vllm"
    cost_per_token: 0.000002     # $0.002 / 1K tokens
```

**Use case**: Development / testing / low-priority requests where cost is the primary concern.

**Caveat**: Ignores performance. A free backend with high latency will still be chosen over a fast paid one.

---

## 5. Session Affinity

**How it works**: Automatically applied when a client sends a `X-Session-Id` header. Subsequent requests with the same session ID route to the same backend.

**Header**: `X-Session-Id: chat-session-abc123`

**Why it matters**: Ollama and vLLM both benefit from KV Cache reuse when consecutive requests hit the same backend. Session affinity maximizes cache hit rate.

**TTL**: Sessions expire after 30 minutes (configurable: `scheduler.session_ttl`).

**Example Workflow**:
```
Request 1: X-Session-Id: session-1 → routed to ollama-local-a (cached)
Request 2: X-Session-Id: session-1 → routed to ollama-local-a (reuse KV cache)
Request 3: X-Session-Id: session-2 → routed to mock-vllm (weighted random)
Request 4: X-Session-Id: session-1 → routed to ollama-local-a (still cached)
```

---

## Strategy Priority (when combined)

When multiple strategies could apply, the priority order is:

```
1. Model-Aware Filter   (non-negotiable — must serve requested model)
2. Health Filter        (non-negotiable — must be healthy)
3. Circuit Check        (non-negotiable — must be CLOSED)
4. Session Affinity     (if X-Session-Id present and cached)
5. Explicit Strategy    (latency | throughput | cost)
6. Weighted Random      (fallback)
```

---

## Client Usage Examples

### curl
```bash
# Latency-priority routing
curl -X POST http://localhost:9000/v1/chat/completions \
  -H "Authorization: Bearer test-key" \
  -H "X-Routing-Strategy: latency" \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen2.5:0.5b","messages":[{"role":"user","content":"你好"}]}'

# Session affinity
curl -X POST http://localhost:9000/v1/chat/completions \
  -H "Authorization: Bearer test-key" \
  -H "X-Session-Id: my-chat-session" \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen2.5:0.5b","messages":[{"role":"user","content":"你好"}]}'
```

### Python
```python
import requests

headers = {
    "Authorization": "Bearer test-key",
    "X-Routing-Strategy": "cost",
    "Content-Type": "application/json",
}

resp = requests.post(
    "http://localhost:9000/v1/chat/completions",
    headers=headers,
    json={"model": "qwen2.5:0.5b", "messages": [{"role": "user", "content": "你好"}]},
)

print(resp.headers["X-Routed-Backend"])  # e.g., "ollama-local-a"
print(resp.headers["X-Routing-Strategy"])  # e.g., "cost"