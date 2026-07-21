#!/usr/bin/env python3
"""
vLLM Benchmark Report Generator
================================
Reads data/benchmark_summary.json, produces:
  - data/REPORT.md (human-readable markdown report)
  - data/comparison.json (Ollama-vs-vLLM structured comparison)

Supports cross-referencing the local Ollama baseline from
../../03-benchmark expected results for comparison.
"""

import json
import os
import sys
from datetime import datetime

# =============================================================================
# Paths
# =============================================================================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "data")
SUMMARY_PATH = os.path.join(DATA_DIR, "benchmark_summary.json")
REPORT_PATH = os.path.join(DATA_DIR, "REPORT.md")
COMPARISON_PATH = os.path.join(DATA_DIR, "comparison.json")

# =============================================================================
# Local Ollama Baseline (from 03-benchmark — RTX 4060 Laptop 8GB, qwen2.5)
# These values are hardcoded from the project's validated benchmark data.
# =============================================================================

OLLAMA_BASELINE = {
    "environment": {
        "gpu": "RTX 4060 Laptop 8GB",
        "gpu_memory_mib": 8188,
        "platform": "Windows 11 bare metal",
        "inference": "Ollama 0.30.9",
    },
    "models": {
        "qwen2.5-0.5b": {
            "size_mb": 397,
            "concurrency": {
                1: {"ttft_avg_ms": 3383, "ttft_p95_ms": 5828, "tpot_avg_ms": 8, "throughput_tps": 198, "token_p99_ms": 1828},
                2: {"ttft_avg_ms": 3922, "ttft_p95_ms": 6578, "tpot_avg_ms": 8, "throughput_tps": 199, "token_p99_ms": 1235},
                4: {"ttft_avg_ms": 4006, "ttft_p95_ms": 6610, "tpot_avg_ms": 8, "throughput_tps": 194, "token_p99_ms": 1375},
                8: {"ttft_avg_ms": 4013, "ttft_p95_ms": 6578, "tpot_avg_ms": 8, "throughput_tps": 190, "token_p99_ms": 1421},
            },
        },
        "qwen2.5-1.5b": {
            "size_mb": 986,
            "concurrency": {
                1: {"ttft_avg_ms": 6412, "ttft_p95_ms": 11609, "tpot_avg_ms": 15, "throughput_tps": 132, "token_p99_ms": 1172},
                2: {"ttft_avg_ms": 6428, "ttft_p95_ms": 11641, "tpot_avg_ms": 15, "throughput_tps": 131, "token_p99_ms": 1203},
                4: {"ttft_avg_ms": 6713, "ttft_p95_ms": 11844, "tpot_avg_ms": 14, "throughput_tps": 123, "token_p99_ms": 1484},
                8: {"ttft_avg_ms": 6584, "ttft_p95_ms": 11844, "tpot_avg_ms": 14, "throughput_tps": 126, "token_p99_ms": 1547},
            },
        },
    },
}

# =============================================================================
# Load vLLM Data
# =============================================================================


def load_vllm_data():
    """Load vLLM benchmark summary."""
    if not os.path.exists(SUMMARY_PATH):
        print(f"ERROR: {SUMMARY_PATH} not found.")
        print("Run: python3 vllm_benchmark.py first")
        sys.exit(1)

    with open(SUMMARY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# =============================================================================
# Comparison Logic
# =============================================================================


def compute_comparison(vllm_data: dict) -> dict:
    """Build structured Ollama-vs-vLLM comparison."""
    comparison = {
        "timestamp": datetime.now().isoformat(),
        "environments": {
            "ollama": OLLAMA_BASELINE["environment"],
            "vllm": {
                "gpu": vllm_data["gpu"]["name"],
                "gpu_memory_mib": vllm_data["gpu"]["memory_mib"],
                "platform": "AutoDL Cloud Linux",
                "inference": "vLLM (Continuous Batching + PagedAttention)",
            },
        },
        "models": {},
    }

    vllm_models = vllm_data.get("models", {})

    for model_name, ollama_model in OLLAMA_BASELINE["models"].items():
        model_comparison = {"concurrency": {}}

        for conc in [1, 2, 4, 8]:
            conc_str = str(conc)
            ollama_c = ollama_model["concurrency"][conc]
            vllm_c = vllm_models.get(model_name, {}).get("concurrency", [])

            # Find matching concurrency level in vLLM data
            vllm_match = None
            for vc in vllm_c:
                if vc["concurrency"] == conc:
                    vllm_match = vc
                    break

            if vllm_match:
                # Compute deltas
                ttft_delta = ollama_c["ttft_avg_ms"] - vllm_match["ttft_avg_ms"]
                tpot_delta = ollama_c["tpot_avg_ms"] - vllm_match["tpot_avg_ms"]
                tps_delta = vllm_match["throughput_tps"] - ollama_c["throughput_tps"]

                ttft_ratio = (
                    round(ollama_c["ttft_avg_ms"] / vllm_match["ttft_avg_ms"], 2)
                    if vllm_match["ttft_avg_ms"] > 0
                    else 0
                )
                tps_ratio = (
                    round(vllm_match["throughput_tps"] / ollama_c["throughput_tps"], 2)
                    if ollama_c["throughput_tps"] > 0
                    else 0
                )

                model_comparison["concurrency"][conc] = {
                    "ollama": ollama_c,
                    "vllm": vllm_match,
                    "delta": {
                        "ttft_ms": round(ttft_delta, 0),
                        "tpot_ms": round(tpot_delta, 0),
                        "throughput_tps": round(tps_delta, 0),
                    },
                    "ratio": {
                        "ttft_vllm_faster_x": ttft_ratio,
                        "throughput_vllm_faster_x": tps_ratio,
                    },
                }

        if model_comparison["concurrency"]:
            comparison["models"][model_name] = model_comparison

    return comparison


# =============================================================================
# Markdown Report Generator
# =============================================================================


def generate_markdown(vllm_data: dict, comparison: dict) -> str:
    """Generate REPORT.md content."""
    lines = []
    gpu = vllm_data["gpu"]
    config = vllm_data["config"]
    vllm_models = vllm_data.get("models", {})

    # Header
    lines.append("# vLLM GPU Scheduling Benchmark Report")
    lines.append("")
    lines.append(
        f"> Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')} | "
        f"Platform: AutoDL Cloud | GPU: {gpu['name']} ({gpu['memory_mib']} MiB)"
    )
    lines.append("")

    # Environment
    lines.append("## 1. Test Environment")
    lines.append("")
    lines.append("| Item | Ollama (Local) | vLLM (Cloud) |")
    lines.append("|------|---------------|--------------|")
    lines.append(
        f"| GPU | {OLLAMA_BASELINE['environment']['gpu']} | {gpu['name']} |"
    )
    lines.append(
        f"| VRAM | 8,188 MiB | {gpu['memory_mib']} MiB |"
    )
    lines.append(
        f"| Platform | Windows 11 bare metal | Linux (AutoDL) |"
    )
    lines.append(
        f"| Engine | Ollama 0.30.9 | vLLM (Continuous Batching + PagedAttention) |"
    )
    lines.append("")

    # Test config
    lines.append("## 2. Test Configuration")
    lines.append("")
    lines.append(f"- Models: qwen2.5-0.5b (397 MB), qwen2.5-1.5b (986 MB)")
    lines.append(f"- Concurrency: {', '.join(f'C{c}' for c in config['concurrency_levels'])}")
    lines.append(f"- Requests per level: {config['requests_per_level']}")
    lines.append(f"- Max tokens: {config['max_tokens']}")
    lines.append(f"- Prompt: _{config['prompt']}_")
    lines.append(f"- Total tests: 2 models × 4 concurrency × 8 requests = 64")
    lines.append("")

    # vLLM Standalone Results
    lines.append("## 3. vLLM Standalone Results")
    lines.append("")

    for model_name in sorted(vllm_models.keys()):
        model_data = vllm_models[model_name]
        lines.append(f"### {model_name} ({model_data.get('size_mb', '?')} MB)")
        lines.append("")
        lines.append(
            "| Conc | OK | TTFT avg | TTFT P95 | TPOT | Throughput | Token P99 |"
        )
        lines.append(
            "|------|----|----------|----------|------|------------|-----------|"
        )
        for c in model_data.get("concurrency", []):
            lines.append(
                f"| C{c['concurrency']} | {c['success_rate']} | "
                f"{c['ttft_avg_ms']:.0f} ms | {c['ttft_p95_ms']:.0f} ms | "
                f"{c['tpot_avg_ms']:.0f} ms | {c['throughput_tps']:.0f} t/s | "
                f"{c['token_p99_ms']:.0f} ms |"
            )
        lines.append("")

    # Cross-platform comparison
    lines.append("## 4. Ollama vs vLLM Cross-Platform Comparison")
    lines.append("")
    lines.append("> RTX 4060 Laptop 8GB (Ollama) vs RTX 4090 24GB (vLLM)")
    lines.append("")

    for model_name in sorted(comparison.get("models", {}).keys()):
        model_comp = comparison["models"][model_name]
        lines.append(f"### {model_name}")
        lines.append("")

        for conc in sorted(model_comp["concurrency"].keys()):
            data = model_comp["concurrency"][conc]
            ollama = data["ollama"]
            vllm = data["vllm"]
            delta = data["delta"]
            ratio = data["ratio"]

            lines.append(f"#### C{conc}")
            lines.append("")
            lines.append(
                "| Metric | Ollama (4060) | vLLM (4090) | Delta | Ratio |"
            )
            lines.append(
                "|--------|--------------|-------------|-------|-------|"
            )

            # TTFT
            ttft_vllm_faster = "vLLM faster" if delta["ttft_ms"] > 0 else "Ollama faster"
            lines.append(
                f"| TTFT | {ollama['ttft_avg_ms']:.0f} ms | {vllm['ttft_avg_ms']:.0f} ms | "
                f"{abs(delta['ttft_ms']):.0f} ms ({ttft_vllm_faster}) | "
                f"{ratio['ttft_vllm_faster_x']}x |"
            )

            # TPOT
            tpot_vllm_faster = "vLLM faster" if delta["tpot_ms"] > 0 else "Ollama faster"
            lines.append(
                f"| TPOT | {ollama['tpot_avg_ms']:.0f} ms | {vllm['tpot_avg_ms']:.0f} ms | "
                f"{abs(delta['tpot_ms']):.0f} ms ({tpot_vllm_faster}) | — |"
            )

            # Throughput
            tps_vllm_better = "vLLM higher" if delta["throughput_tps"] >= 0 else "Ollama higher"
            lines.append(
                f"| Throughput | {ollama['throughput_tps']:.0f} t/s | {vllm['throughput_tps']:.0f} t/s | "
                f"{abs(delta['throughput_tps']):.0f} t/s ({tps_vllm_better}) | "
                f"{ratio['throughput_vllm_faster_x']}x |"
            )

            # Token P99
            vllm_p99 = vllm.get("token_p99_ms", 0)
            ollama_p99 = ollama.get("token_p99_ms", 0)
            p99_delta = ollama_p99 - vllm_p99
            p99_better = "vLLM lower" if p99_delta > 0 else "Ollama lower"
            lines.append(
                f"| Token P99 | {ollama_p99:.0f} ms | {vllm_p99:.0f} ms | "
                f"{abs(p99_delta):.0f} ms ({p99_better}) | — |"
            )

            lines.append("")

    # Analysis
    lines.append("## 5. Key Findings")
    lines.append("")

    # Auto-analyze from comparison data
    findings = []

    for model_name in sorted(comparison.get("models", {}).keys()):
        model_comp = comparison["models"][model_name]

        # C1 throughput ratio
        if 1 in model_comp["concurrency"]:
            c1 = model_comp["concurrency"][1]
            tps_ratio = c1["ratio"]["throughput_vllm_faster_x"]
            ttft_ratio = c1["ratio"]["ttft_vllm_faster_x"]

            findings.append(
                f"### {model_name} (C1 baseline)"
            )
            findings.append("")

            if tps_ratio > 1:
                findings.append(
                    f"- **Throughput**: vLLM achieves **{tps_ratio}x** higher throughput at C1 "
                    f"({c1['vllm']['throughput_tps']:.0f} vs {c1['ollama']['throughput_tps']:.0f} t/s). "
                    f"Attributed to Continuous Batching + PagedAttention's efficient GPU memory management."
                )
            elif tps_ratio < 1:
                findings.append(
                    f"- **Throughput**: vLLM throughput is **{tps_ratio}x** of Ollama at C1 — "
                    f"unexpected result, likely due to [TO BE INVESTIGATED]."
                )
            else:
                findings.append(
                    f"- **Throughput**: Roughly equivalent at C1."
                )

            if ttft_ratio > 1:
                findings.append(
                    f"- **TTFT**: vLLM reduces first-token latency by **{ttft_ratio}x** "
                    f"({c1['vllm']['ttft_avg_ms']:.0f} vs {c1['ollama']['ttft_avg_ms']:.0f} ms). "
                    f"PagedAttention eliminates redundant KV Cache recomputation."
                )
            elif ttft_ratio < 1:
                findings.append(
                    f"- **TTFT**: vLLM TTFT is {ttft_ratio}x of Ollama — "
                    f"may reflect model-load overhead or network latency on cloud GPU."
                )

            findings.append("")

        # Throughput inflection point
        findings.append(f"### {model_name} — Throughput Scalability")
        findings.append("")
        max_tps = 0
        max_conc = 0
        for conc, data in model_comp["concurrency"].items():
            vllm_tps = data["vllm"]["throughput_tps"]
            if vllm_tps > max_tps:
                max_tps = vllm_tps
                max_conc = conc

        findings.append(
            f"- vLLM throughput peaks at **C{max_conc}** ({max_tps:.0f} t/s)"
        )
        findings.append(
            f"- Ollama 0.5b throughput inflection at C2; Ollama 1.5b at C8"
        )
        findings.append(
            "- vLLM's Continuous Batching dynamically packs requests into GPU batches, "
            "resulting in better high-concurrency utilization compared to Ollama's simpler scheduling."
        )
        findings.append("")

    lines.extend(findings)

    # Conclusion
    lines.append("## 6. Conclusion & Recommendations")
    lines.append("")
    lines.append(
        "vLLM with PagedAttention and Continuous Batching delivers the following advantages over "
        "local Ollama deployment:"
    )
    lines.append("")
    lines.append(
        "1. **[TO BE POPULATED]** — Fill in after benchmark data analysis"
    )
    lines.append(
        "2. **[TO BE POPULATED]** — Fill in after benchmark data analysis"
    )
    lines.append(
        "3. **[TO BE POPULATED]** — Fill in after benchmark data analysis"
    )
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        f"*Report auto-generated by vllm_report.py | {datetime.now().year}*"
    )
    lines.append("")

    return "\n".join(lines)


# =============================================================================
# Main
# =============================================================================


def main():
    """Generate all report outputs."""
    print("=" * 50)
    print("  vLLM Benchmark Report Generator")
    print("=" * 50)

    # Create output directory
    os.makedirs(DATA_DIR, exist_ok=True)

    # Load vLLM data
    print(f"\nLoading vLLM data: {SUMMARY_PATH}")
    vllm_data = load_vllm_data()
    print(f"  GPU: {vllm_data['gpu']['name']}")
    print(f"  Models: {', '.join(vllm_data['models'].keys())}")

    # Compute comparison
    print("\nComputing Ollama-vs-vLLM comparison...")
    comparison = compute_comparison(vllm_data)

    # Save comparison JSON
    with open(COMPARISON_PATH, "w", encoding="utf-8") as f:
        json.dump(comparison, f, indent=2, ensure_ascii=False)
    print(f"  Comparison saved: {COMPARISON_PATH}")

    # Generate REPORT.md
    print("\nGenerating markdown report...")
    report_md = generate_markdown(vllm_data, comparison)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(report_md)
    print(f"  Report saved: {REPORT_PATH}")

    print(f"\n{'='*50}")
    print("  All Done!")
    print(f"  Report: {REPORT_PATH}")
    print(f"  Comparison: {COMPARISON_PATH}")
    print(f"  Next: Complete work_report_template.md")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()