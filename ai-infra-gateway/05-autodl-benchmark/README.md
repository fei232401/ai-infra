# 05 — AutoDL + vLLM GPU Scheduling Benchmark

Cloud GPU inference benchmark with vLLM (Continuous Batching + PagedAttention), comparing against local Ollama baseline.

## AutoDL Rental Configuration

| Item | Specification | Reason |
|------|--------------|--------|
| **GPU** | RTX 4090 24GB × 1 | Full qwen2.5 0.5b–7b support, FP8 native, best vLLM compatibility |
| **RAM** | 32GB | Headroom for vLLM + model loading + KV Cache |
| **System Disk** | 60GB | Image (~20GB) + qwen2.5-7b (~15GB) + workspace |
| **CUDA** | ≥12.1 | vLLM minimum requirement |
| **Image** | PyTorch 2.1+ / CUDA 12.x | Pre-installed — reduce setup time |
| **Backup GPU** | RTX 3090 24GB | Budget option, slightly slower FP8 |
| **Budget (4090)** | ~¥3–5/hour | Typical AutoDL pricing |

## Execution Workflow

```
SSH into instance
    │
    ▼
bash vllm_deploy.sh              ← 15 min: pip install → download models → start server
    │
    ▼
python vllm_benchmark.py         ← 20 min: C1-C8 gradient, dual model
    │
    ▼
python vllm_report.py            ←  1 min: statistics + charts + REPORT.md
    │
    ▼
fill work_report_template.md     ← 10 min: paste data, write analysis
```

## Scripts

| Script | Version | Purpose | Runtime |
|--------|---------|---------|---------|
| `vllm_deploy.sh` | v1.0 | Install vLLM, download models, verify server | ~15 min |
| `vllm_benchmark.py` | **v3.0** | SRE-LAB aligned: 3-tier scenario + step-pressure ramp | ~30 min |
| `vllm_report.py` | v2.0 | Statistics + latency distribution + Ollama-vs-vLLM comparison | ~1 min |
| `work_report_template.md` | **v2.0** | SRE-LAB formatted Chinese work report with YAML template | — |

### v3.0 Upgrade (SRE-LAB Alignment)

| Feature | v2.0 (Legacy) | v3.0 (Current) |
|---------|--------------|----------------|
| Concurrency | Fixed C1/C2/C4/C8 | Step-pressure ramp (1→4→8→16→32, 60s/step) |
| Scenarios | Single prompt | 3-tier (light 50% / medium 30% / heavy 15% / health 5%) |
| Metrics | TTFT, TPOT, TPS, Token P99 | + RPS (requests/sec) per scenario |
| Cross-project | N/A | Methodologically aligned with SRE-LAB benchmark suite |

## Output Files (auto-generated in `data/`)

| File | Content |
|------|---------|
| `raw_benchmark.json` | Raw per-request latency data |
| `benchmark_summary.json` | Aggregated TTFT/TPOT/Throughput/P99 |
| `REPORT.md` | Human-readable comparison report |
| `comparison.json` | Ollama-vs-vLLM structured comparison data |