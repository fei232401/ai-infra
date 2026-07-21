# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [2.0.0] — 2026-06-21

### Added
- **JWT Authentication** — HS256-signed tokens with configurable expiration (60min default), replacing hardcoded API Key
- **Circuit Breaker** — 3-failure threshold → OPEN → 30s timeout → HALF_OPEN → CLOSED recovery cycle
- **Request-Level Retry** — Automatic up-to-2 retries with 1s backoff on Ollama backend failures
- **Token Issuance Endpoint** — `POST /api/auth/token` returns `{access_token, token_type, expires_in}`
- **Prometheus Alerting Rules** — 6 rules covering GPU temperature (80°C/87°C), memory >90%, Ollama down, rate limiting, circuit breaker open
- **Dual-Model Benchmark** — 0.5B vs 1.5B C1-C8 comparison with TTFT/TPOT/Throughput/Token P99
- **PROJECT_NARRATIVE.md** — 11-chapter comprehensive project story

### Changed
- Authentication: `auth_middleware` now supports JWT-first with API Key fallback compatibility
- Configuration: `gateway_config.yaml` expanded with `jwt_*`, `circuit_breaker`, `retry` sections
- Health endpoint: Now includes `circuit_breaker` state in response
- Project structure: Reorganized from flat `scripts/` to numbered modules (`01-gateway-server/`, `02-dashboard/`, `03-benchmark/`, `04-infrastructure/`)

### Fixed
- Gateway startup CWD issue — `start_gateway.py` forces absolute paths
- Ollama Registry GFW bypass — GGUF header validation + Modelfile local import workaround
- requirements.txt GBK encoding — Migrated to pure ASCII

---

## [1.0.0] — 2026-06-20

### Added
- **Inference API Gateway** — FastAPI server with Bearer Token auth, Token Bucket rate limiter, SSE streaming proxy
- **GPU Dashboard** — Dark-themed real-time monitoring with pynvml + matplotlib (4 panels, 3s refresh)
- **Benchmark Framework** — asyncio + aiohttp concurrency testing with TTFT/TPOT/Throughput collection
- **Environment Diagnostics** — 5-layer hardware virtualization diagnostic (WMIC → CPUID → Hypervisor → VBS → MSR)
- **Model Import Tool** — ModelScope GGUF download + Ollama `create` local import
- **WSL2 Enable Script** — PowerShell admin script for Virtual Machine Platform + WSL feature installation
- **Project Documentation** — README, troubleshooting log (5 T-xxx records), final report