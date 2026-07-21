# 03 — LLM Inference Benchmark Framework

Full-dimension LLM inference benchmarking: TTFT / TPOT / Throughput / Token latency distribution / Concurrency-throughput inflection analysis.

## Files

| File | Purpose |
|------|---------|
| `benchmark_final.py` | ★ Primary benchmark — dual-model, multi-concurrency gradient, three-in-one report |
| `benchmark.py` | Initial version (basic single-model benchmark, retained for history) |
| `gateway_verify.py` | Gateway functional verification (auth, rate limiting, SSE streaming) |

## Quick Start

```powershell
cd 03-benchmark
python benchmark_final.py
```

## Test Matrix

| Parameter | Value |
|-----------|-------|
| Models | qwen2.5:0.5b (397 MB), qwen2.5:1.5b (986 MB) |
| Concurrency gradient | 1, 2, 4, 8 |
| Requests per level | 8 |
| Output limit | 120 tokens |
| Total tests | 2 models × 4 concurrency × 8 requests = 64 |

## Output Reports

| Report | Contents |
|--------|----------|
| Performance baseline table | TTFT (avg/P95), TPOT, Throughput, VRAM, Token P99 per concurrency level |
| Token latency distribution | Mean, Median, P50, P90, P95, P99 + text histogram |
| Concurrency-throughput inflection | C1→C8 throughput growth rate per model + inflection point annotation |

## Architecture

```
asyncio + aiohttp → async concurrent HTTP client
├── POST → Gateway /api/chat/stream (SSE)
├── Per-line SSE parsing → collect TTFT + Token latencies
├── statistics module → avg / P95 / P99 computation
└── Three-in-one report:
    ├── Performance baseline (concurrency vs TTFT/TPOT/Throughput)
    ├── Token-level latency distribution (P50-P99 + histogram)
    └── Concurrency-throughput inflection analysis (marginal gain)
```

## Metrics Definitions

| Metric | Full Name | What It Measures |
|--------|-----------|-----------------|
| **TTFT** | Time To First Token | Elapsed time from request to first token returned |
| **TPOT** | Time Per Output Token | Average interval between consecutive tokens during generation |
| **Throughput** | Total Throughput | System-wide tokens/second output rate |
| **Token P99** | Token Latency P99 | 99th percentile latency for individual tokens |

## Design Decisions

### Why asyncio + aiohttp Instead of a Benchmarking Framework (e.g., Locust)?

The gateway itself is built on asyncio + aiohttp. Using the same stack for benchmarking eliminates tooling impedance — the benchmark client and the gateway server share the same concurrency model, making latency measurements directly comparable and reducing variables in analysis.

### Why SSE Stream Parsing Instead of Simple HTTP Timing?

LLM inference is a streaming workload. Simple request-response timing (time-to-last-byte) would hide the distinction between TTFT (model loading + first token) and TPOT (generation speed). By parsing the SSE stream token-by-token, we can attribute latency to the correct phase.

### Why C1-C8 Gradient with 8 Requests per Level?

C1 establishes the single-request baseline. C2 tests whether the GPU can overlap two requests. C4 and C8 push into concurrency saturation, finding the throughput inflection point — the concurrency level beyond which adding more requests doesn't increase total throughput. 8 requests per level provides enough samples for statistically meaningful P95/P99 without excessive test duration.

### Why Three Reports in One Script?

The reports answer three distinct questions: (1) "How fast?" (baseline), (2) "How consistent?" (distribution), (3) "How scalable?" (inflection). Running them as a single script ensures all data comes from the same test conditions — eliminating cross-run variance.