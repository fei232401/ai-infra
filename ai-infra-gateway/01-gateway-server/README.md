# 01 — Inference API Gateway

Async FastAPI inference gateway, proxies Ollama with enterprise-grade API management.

## Files

| File | Purpose |
|------|---------|
| `gateway_server.py` | Core gateway: JWT + API Key dual-mode auth, Token Bucket rate limiter, circuit breaker, retry logic, SSE streaming proxy |
| `start_gateway.py` | Launcher script — forces absolute paths to solve Windows CWD issues |
| `config/gateway_config.yaml` | Configuration for auth, rate limiting, circuit breaker, retry, and Ollama backend |

## v2.0 Upgrades

- ✅ JWT Authentication — HS256-signed tokens, 60min expiry, replacing hardcoded API Keys
- ✅ Circuit Breaker — 3 consecutive failures → OPEN → 30s timeout → HALF_OPEN → CLOSED
- ✅ Request-level retry — Up to 2 automatic retries with 1s backoff on Ollama failures
- ✅ Token issuance endpoint — `POST /api/auth/token` returns `{access_token, token_type, expires_in}`

## Quick Start

```powershell
cd 01-gateway-server
python start_gateway.py
# Swagger docs: http://localhost:8000/docs
# Health check:  http://localhost:8000/health
```

## Design Decisions

### Why Token Bucket instead of Leaky Bucket?

Token Bucket allows burst traffic (configurable via `capacity`) while enforcing an average rate (`refill_rate`). This fits LLM inference workloads where clients may send bursts of concurrent requests. Leaky Bucket smooths all traffic to a constant rate, which would delay legitimate requests unnecessarily.

### Why SSE instead of WebSocket?

LLM token generation is inherently one-way (server → client). SSE is HTTP-native, requires no protocol upgrade, and is simpler to implement and debug. WebSocket's full-duplex capability adds complexity with no benefit for this use case.

### Why JWT HS256 instead of OAuth/OIDC?

For a self-contained gateway with no external identity provider, symmetric JWT (HS256) provides sufficient security with minimal infrastructure. The shared secret is configured in `gateway_config.yaml`. OAuth/OIDC would require an external IdP, which conflicts with the "no Docker, no external services" constraint.

### Gateway Resilience Architecture

```
Request → Auth Middleware → Token Bucket → Circuit Breaker → Ollama Backend
                │               │               │                 │
                ▼               ▼               ▼                 ▼
             401 reject      429 reject      503 reject      retry (2x)
```

The middleware chain is layered: auth failure stops at the first barrier, rate limiting protects the backend, and the circuit breaker prevents cascading failures when Ollama is degraded.

## Verified

- ✅ Auth — no key → 401, correct key → 200, JWT token → 200
- ✅ Token Bucket — capacity=10, refill_rate=5/s, 12 concurrent triggers 429
- ✅ Circuit Breaker — OPEN after 3 failures, HALF_OPEN probe, CLOSED recovery
- ✅ SSE streaming — per-token push to client verified