#!/usr/bin/env python3
"""
vLLM Inference Benchmark — AutoDL Cloud GPU (v3.0 — SRE-LAB Aligned)
=====================================================================
Upgraded with 3-scenario tiered testing (light/medium/heavy) +
step-pressure concurrency ramp — aligned with SRE-LAB methodology.

Metrics:  TTFT, TPOT, Throughput, Token P99, RPS
Scenarios: Short prompt (50 tokens) / Medium prompt (200 tokens) / Long prompt (500 tokens)
Pressure:  Step-ladder concurrency ramp (1→4→8→16→32) with configurable step duration
Output:   data/raw_benchmark.json + data/benchmark_summary.json
"""

import asyncio
import aiohttp
import json
import time
import statistics
import os
import sys
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional

# =============================================================================
# Configuration
# =============================================================================

VLLM_BASE_URL = "http://localhost:8000"
API_ENDPOINT = f"{VLLM_BASE_URL}/v1/chat/completions"

# Models to benchmark
MODEL_CONFIGS = [
    {
        "name": "qwen2.5-0.5b",
        "size_mb": 397,
        "vllm_model": None,
    },
    {
        "name": "qwen2.5-1.5b",
        "size_mb": 986,
        "vllm_model": None,
    },
]

# --- SRE-LAB Aligned: 3 Scenario Tiers ---
# Weights: light=50%, medium=30%, heavy=15%, health=5% (per SRE-LAB)
SCENARIOS = [
    {
        "name": "light_inference",
        "weight": 50,
        "prompt": "Explain what a GPU is in one sentence.",
        "max_tokens": 50,
        "label": "Light — short prompt, quick generation",
    },
    {
        "name": "medium_inference",
        "weight": 30,
        "prompt": "Explain GPU memory hierarchy including registers, shared memory, L1/L2 cache, and HBM in detail.",
        "max_tokens": 200,
        "label": "Medium — moderate prompt, mid-range generation",
    },
    {
        "name": "heavy_inference",
        "weight": 15,
        "prompt": "Explain GPU memory hierarchy, KV cache, PagedAttention, FlashAttention, and continuous batching in depth. Discuss how each technology improves LLM inference throughput and latency. Compare the approaches used by vLLM, TensorRT-LLM, and llama.cpp.",
        "max_tokens": 500,
        "label": "Heavy — long prompt, extended generation",
    },
    {
        "name": "health_check",
        "weight": 5,
        "prompt": "Say hello.",
        "max_tokens": 5,
        "label": "Health — minimal API probe",
    },
]

# --- SRE-LAB Aligned: Step-Pressure Concurrency Ramp ---
# Instead of fixed C1/C2/C4/C8, use a configurable step ladder
STEP_PRESSURE_CONFIG = {
    "enabled": True,  # Set False for legacy fixed-concurrency mode
    "start_users": 1,
    "step_users": 4,  # Add N users each step
    "step_time_s": 60,  # Seconds per step (SRE-LAB uses 60s)
    "max_users": 32,  # Upper bound
    "requests_per_step": 32,  # Requests to dispatch within each step
}

# Legacy fixed-concurrency mode (used when STEP_PRESSURE_CONFIG.enabled=False)
CONCURRENCY_LEVELS = [1, 2, 4, 8]
REQUESTS_PER_LEVEL = 8
MAX_TOKENS_DEFAULT = 120

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# =============================================================================
# Data Structures
# =============================================================================


@dataclass
class RequestResult:
    """Per-request latency metrics."""

    scenario: str
    concurrency: int
    request_id: int
    ttft_ms: float
    tpot_ms: float
    total_latency_ms: float
    token_count: int
    token_latencies_ms: list = field(default_factory=list)
    success: bool = True
    error: Optional[str] = None


@dataclass
class ConcurrencyResult:
    """Aggregated metrics for one concurrency level."""

    concurrency: int
    success_count: int
    total_count: int
    ttft_avg_ms: float
    ttft_p95_ms: float
    tpot_avg_ms: float
    throughput_tps: float
    token_p99_ms: float
    rps: float  # Requests per second
    all_token_latencies: list = field(default_factory=list)


@dataclass
class ScenarioResult:
    """Results for one scenario across concurrency levels."""

    scenario_name: str
    scenario_label: str
    weight: int
    concurrency_results: list = field(default_factory=list)


@dataclass
class ModelResult:
    """Complete benchmark for one model."""

    model_name: str
    model_size_mb: int
    gpu_name: str
    gpu_memory_mib: int
    timestamp: str
    mode: str  # "step_pressure" or "fixed_concurrency"
    scenario_results: list = field(default_factory=list)


# =============================================================================
# GPU Info
# =============================================================================


def get_gpu_info():
    """Get GPU name and memory from nvidia-smi."""
    import subprocess

    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total",
                "--format=csv,noheader",
            ],
            encoding="utf-8",
        ).strip()
        name, mem = out.split(", ")
        mem_mib = int(mem.replace(" MiB", ""))
        return name.strip(), mem_mib
    except Exception:
        return "Unknown GPU", 0


# =============================================================================
# Async Benchmark Core
# =============================================================================


async def send_request(
    session: aiohttp.ClientSession,
    model: str,
    scenario: dict,
    concurrency: int,
    request_id: int,
    semaphore: asyncio.Semaphore,
) -> RequestResult:
    """Send one streaming chat completion request and collect metrics."""
    async with semaphore:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": scenario["prompt"]}],
            "max_tokens": scenario["max_tokens"],
            "stream": True,
        }

        t_start = time.monotonic()
        ttft = None
        token_count = 0
        token_latencies = []
        prev_token_time = t_start

        try:
            async with session.post(
                API_ENDPOINT, json=payload, timeout=aiohttp.ClientTimeout(total=300)
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    return RequestResult(
                        scenario=scenario["name"],
                        concurrency=concurrency,
                        request_id=request_id,
                        ttft_ms=0,
                        tpot_ms=0,
                        total_latency_ms=0,
                        token_count=0,
                        success=False,
                        error=f"HTTP {resp.status}: {body[:200]}",
                    )

                async for line in resp.content:
                    line = line.decode("utf-8").strip()
                    if not line or line.startswith(":"):
                        continue
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                            if "choices" in chunk and len(chunk["choices"]) > 0:
                                delta = chunk["choices"][0].get("delta", {})
                                content = delta.get("content", "")
                                if content:
                                    now = time.monotonic()
                                    if ttft is None:
                                        ttft = (now - t_start) * 1000
                                    else:
                                        token_latency = (now - prev_token_time) * 1000
                                        token_latencies.append(token_latency)
                                    prev_token_time = now
                                    token_count += 1
                        except json.JSONDecodeError:
                            continue

            t_end = time.monotonic()
            total_latency = (t_end - t_start) * 1000

            if token_count == 0:
                return RequestResult(
                    scenario=scenario["name"],
                    concurrency=concurrency,
                    request_id=request_id,
                    ttft_ms=total_latency,
                    tpot_ms=0,
                    total_latency_ms=total_latency,
                    token_count=0,
                    success=False,
                    error="No tokens received",
                )

            if len(token_latencies) > 0:
                tpot = statistics.mean(token_latencies)
            else:
                tpot = 0

            return RequestResult(
                scenario=scenario["name"],
                concurrency=concurrency,
                request_id=request_id,
                ttft_ms=round(ttft or total_latency, 2),
                tpot_ms=round(tpot, 2),
                total_latency_ms=round(total_latency, 2),
                token_count=token_count,
                token_latencies_ms=[round(x, 2) for x in token_latencies],
                success=True,
            )

        except asyncio.TimeoutError:
            return RequestResult(
                scenario=scenario["name"],
                concurrency=concurrency,
                request_id=request_id,
                ttft_ms=0,
                tpot_ms=0,
                total_latency_ms=0,
                token_count=0,
                success=False,
                error="Timeout",
            )
        except Exception as e:
            return RequestResult(
                scenario=scenario["name"],
                concurrency=concurrency,
                request_id=request_id,
                ttft_ms=0,
                tpot_ms=0,
                total_latency_ms=0,
                token_count=0,
                success=False,
                error=str(e)[:200],
            )


def aggregate_results(
    concurrency: int, results: list[RequestResult]
) -> ConcurrencyResult:
    """Compute aggregate statistics from per-request results."""
    successful = [r for r in results if r.success]
    all_tokens = []
    for r in successful:
        all_tokens.extend(r.token_latencies_ms)

    total_tokens = sum(r.token_count for r in successful)
    total_time_s = (
        max(r.total_latency_ms for r in successful) / 1000 if successful else 1
    )

    p95_ttft = 0
    if len(successful) >= 2:
        ttft_sorted = sorted(r.ttft_ms for r in successful)
        p95_idx = int(len(ttft_sorted) * 0.95)
        p95_ttft = ttft_sorted[min(p95_idx, len(ttft_sorted) - 1)]

    token_p99 = 0
    if len(all_tokens) >= 2:
        token_sorted = sorted(all_tokens)
        p99_idx = int(len(token_sorted) * 0.99)
        token_p99 = token_sorted[min(p99_idx, len(token_sorted) - 1)]

    avg_ttft = statistics.mean(r.ttft_ms for r in successful) if successful else 0
    avg_tpot = statistics.mean(r.tpot_ms for r in successful) if successful else 0
    throughput = total_tokens / total_time_s if total_time_s > 0 else 0
    rps = len(successful) / total_time_s if total_time_s > 0 else 0

    return ConcurrencyResult(
        concurrency=concurrency,
        success_count=len(successful),
        total_count=len(results),
        ttft_avg_ms=round(avg_ttft, 1),
        ttft_p95_ms=round(p95_ttft, 1),
        tpot_avg_ms=round(avg_tpot, 1),
        throughput_tps=round(throughput, 1),
        token_p99_ms=round(token_p99, 1),
        rps=round(rps, 2),
        all_token_latencies=[round(x, 1) for x in all_tokens],
    )


# =============================================================================
# Step-Pressure Mode (SRE-LAB Aligned)
# =============================================================================


async def run_step_pressure(
    session: aiohttp.ClientSession,
    model: str,
    scenarios: list[dict],
    config: dict,
) -> list[ScenarioResult]:
    """
    Execute step-ladder concurrency ramp across all scenarios.

    For each scenario, ramp concurrency from start_users to max_users
    in steps of step_users, holding each step for step_time_s seconds.
    """
    all_scenario_results = []

    for scenario in scenarios:
        print(f"\n  Scenario: {scenario['name']} ({scenario['label']})")
        print(f"  Weight: {scenario['weight']}% | Max tokens: {scenario['max_tokens']}")

        scenario_result = ScenarioResult(
            scenario_name=scenario["name"],
            scenario_label=scenario["label"],
            weight=scenario["weight"],
        )

        concurrency = config["start_users"]
        while concurrency <= config["max_users"]:
            # Weighted request count per step
            n_requests = int(config["requests_per_step"] * scenario["weight"] / 100)
            n_requests = max(n_requests, 4)  # Minimum 4 requests per step

            print(f"    Concurrency={concurrency} ({n_requests} requests, {config['step_time_s']}s)")

            semaphore = asyncio.Semaphore(concurrency)
            t_step_start = time.monotonic()

            # Launch requests (the semaphore limits actual concurrency)
            tasks = [
                send_request(session, model, scenario, concurrency, i, semaphore)
                for i in range(n_requests)
            ]
            results = await asyncio.gather(*tasks)

            t_step_end = time.monotonic()
            step_duration = t_step_end - t_step_start

            # Aggregate
            agg = aggregate_results(concurrency, list(results))
            scenario_result.concurrency_results.append(agg)

            # Print step summary
            ok = agg.success_count
            total = agg.total_count
            print(
                f"      OK: {ok}/{total}  |  "
                f"TTFT: {agg.ttft_avg_ms:.0f}ms (P95:{agg.ttft_p95_ms:.0f}ms)  |  "
                f"TPOT: {agg.tpot_avg_ms:.0f}ms  |  "
                f"TPS: {agg.throughput_tps:.0f}  |  "
                f"RPS: {agg.rps:.1f}  |  "
                f"Tok P99: {agg.token_p99_ms:.0f}ms"
            )

            # Wait for remaining step time if we finished early
            if step_duration < config["step_time_s"]:
                await asyncio.sleep(config["step_time_s"] - step_duration)

            concurrency += config["step_users"]

        all_scenario_results.append(scenario_result)

    return all_scenario_results


# =============================================================================
# Fixed Concurrency Mode (Legacy)
# =============================================================================


async def run_fixed_concurrency(
    session: aiohttp.ClientSession,
    model: str,
    concurrency_levels: list[int],
    n_requests: int,
    prompt: str,
    max_tokens: int,
) -> list[ConcurrencyResult]:
    """Run fixed C1-C8 benchmark with a single prompt."""
    scenario = {"name": "fixed", "prompt": prompt, "max_tokens": max_tokens}
    results_list = []

    for concurrency in concurrency_levels:
        print(f"    Concurrency={concurrency} (N={n_requests})")
        semaphore = asyncio.Semaphore(concurrency)
        tasks = [
            send_request(session, model, scenario, concurrency, i, semaphore)
            for i in range(n_requests)
        ]
        results = await asyncio.gather(*tasks)
        agg = aggregate_results(concurrency, list(results))
        results_list.append(agg)

        ok = agg.success_count
        print(
            f"      OK: {ok}/{n_requests}  |  "
            f"TTFT: {agg.ttft_avg_ms:.0f}ms  |  TPS: {agg.throughput_tps:.0f}"
        )

    return results_list


# =============================================================================
# Model Switcher
# =============================================================================


def switch_vllm_model(model_path: str, timeout: int = 120):
    """Kill existing vLLM, restart with new model, wait for ready."""
    import subprocess

    print(f"  Switching vLLM to model: {model_path}")

    subprocess.run(
        ["pkill", "-f", "vllm.entrypoints.openai"],
        capture_output=True,
        timeout=10,
    )
    time.sleep(3)

    subprocess.Popen(
        [
            "nohup",
            "python3",
            "-m",
            "vllm.entrypoints.openai.api_server",
            "--model",
            model_path,
            "--trust-remote-code",
            "--gpu-memory-utilization",
            "0.90",
            "--max-model-len",
            "4096",
            "--port",
            "8000",
        ],
        stdout=open("/tmp/vllm_server.log", "a"),
        stderr=subprocess.STDOUT,
    )

    print("  Waiting for server...", end="", flush=True)
    waited = 0
    while waited < timeout:
        try:
            r = __import__("urllib.request").request.urlopen(
                "http://localhost:8000/health", timeout=2
            )
            if r.status == 200:
                print(" ready!")
                time.sleep(5)
                return True
        except Exception:
            pass
        time.sleep(3)
        waited += 3
        print(".", end="", flush=True)

    print(" FAILED!")
    return False


# =============================================================================
# Main Benchmark Entry Point
# =============================================================================


async def benchmark_model(
    model_config: dict,
    gpu_name: str,
    gpu_mem_mib: int,
    mode: str,
    step_config: dict,
    scenarios: list[dict],
) -> ModelResult:
    """Run full benchmark for one model."""
    model_name = model_config["name"]
    model_path = model_config["vllm_model"]

    print(f"\n{'=' * 60}")
    print(f"  Model: {model_name} ({model_config['size_mb']} MB)")
    print(f"  Mode: {mode}")
    print(f"{'=' * 60}")

    result = ModelResult(
        model_name=model_name,
        model_size_mb=model_config["size_mb"],
        gpu_name=gpu_name,
        gpu_memory_mib=gpu_mem_mib,
        timestamp=datetime.now().isoformat(),
        mode=mode,
    )

    async with aiohttp.ClientSession() as session:
        if mode == "step_pressure":
            scenario_results = await run_step_pressure(
                session, model_path, scenarios, step_config
            )
            result.scenario_results = scenario_results
        else:
            # Legacy fixed-concurrency mode with single prompt
            for conc_results in [await run_fixed_concurrency(
                session,
                model_path,
                CONCURRENCY_LEVELS,
                REQUESTS_PER_LEVEL,
                SCENARIOS[1]["prompt"],
                MAX_TOKENS_DEFAULT,
            )]:
                # Wrap as a single scenario
                sr = ScenarioResult(
                    scenario_name="default",
                    scenario_label="Fixed concurrency benchmark",
                    weight=100,
                    concurrency_results=conc_results,
                )
                result.scenario_results = [sr]

    return result


def find_model_path(short_name: str) -> Optional[str]:
    """Find model path from ~/models/ directory."""
    import glob

    candidates = glob.glob(
        os.path.expanduser(f"~/models/**/*{short_name}*Instruct*"),
        recursive=True,
    )
    dirs = [c for c in candidates if os.path.isdir(c)]
    if dirs:
        return dirs[0]
    return None


def main():
    """Main entry point."""
    mode = "step_pressure" if STEP_PRESSURE_CONFIG["enabled"] else "fixed_concurrency"

    print("=" * 60)
    print(f"  vLLM Inference Benchmark v3.0 — SRE-LAB Aligned")
    print(f"  Mode: {mode}")
    print(f"  AutoDL Cloud GPU | vLLM OpenAI API")
    print("=" * 60)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # GPU info
    gpu_name, gpu_mem_mib = get_gpu_info()
    print(f"\nGPU: {gpu_name} ({gpu_mem_mib} MiB)")

    # Verify vLLM
    print(f"Checking vLLM health at {VLLM_BASE_URL}/health...")
    try:
        import urllib.request
        urllib.request.urlopen(f"{VLLM_BASE_URL}/health", timeout=5)
        print("vLLM server: OK")
    except Exception as e:
        print(f"ERROR: Cannot reach vLLM server: {e}")
        print("Run: bash vllm_deploy.sh first")
        sys.exit(1)

    # Find models
    for model in MODEL_CONFIGS:
        path = find_model_path(model["name"])
        if path:
            model["vllm_model"] = path
            print(f"Found model: {model['name']} → {path}")
        else:
            print(f"WARNING: Model {model['name']} not found in ~/models/, skipping")

    available_models = [m for m in MODEL_CONFIGS if m["vllm_model"]]
    if not available_models:
        print("ERROR: No models found. Run bash vllm_deploy.sh first.")
        sys.exit(1)

    # Print scenario summary
    print(f"\nScenarios (SRE-LAB aligned):")
    for s in SCENARIOS:
        print(f"  {s['name']} ({s['weight']}%) — {s['label']}")

    # Run benchmarks
    all_results = []
    for i, model_config in enumerate(available_models):
        if not switch_vllm_model(model_config["vllm_model"]):
            print(f"FAILED to switch vLLM to {model_config['name']}, skipping")
            continue

        model_result = asyncio.run(
            benchmark_model(
                model_config,
                gpu_name,
                gpu_mem_mib,
                mode,
                STEP_PRESSURE_CONFIG,
                SCENARIOS,
            )
        )
        all_results.append(model_result)

    # Save raw data
    raw_path = os.path.join(OUTPUT_DIR, "raw_benchmark.json")
    raw_data = {r.model_name: asdict(r) for r in all_results}
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(raw_data, f, indent=2, ensure_ascii=False)
    print(f"\nRaw data saved: {raw_path}")

    # Save summary
    summary_path = os.path.join(OUTPUT_DIR, "benchmark_summary.json")
    summary = {
        "gpu": {"name": gpu_name, "memory_mib": gpu_mem_mib},
        "timestamp": datetime.now().isoformat(),
        "mode": mode,
        "config": {
            "step_pressure": STEP_PRESSURE_CONFIG if mode == "step_pressure" else None,
            "scenarios": [
                {"name": s["name"], "weight": s["weight"], "max_tokens": s["max_tokens"]}
                for s in SCENARIOS
            ],
        },
        "models": {},
    }

    for r in all_results:
        summary["models"][r.model_name] = {
            "size_mb": r.model_size_mb,
            "mode": r.mode,
            "scenarios": [],
        }
        for sr in r.scenario_results:
            scenario_data = {
                "scenario": sr.scenario_name,
                "label": sr.scenario_label,
                "weight": sr.weight,
                "concurrency": [],
            }
            for c in sr.concurrency_results:
                scenario_data["concurrency"].append(
                    {
                        "concurrency": c.concurrency,
                        "success_rate": f"{c.success_count}/{c.total_count}",
                        "ttft_avg_ms": c.ttft_avg_ms,
                        "ttft_p95_ms": c.ttft_p95_ms,
                        "tpot_avg_ms": c.tpot_avg_ms,
                        "throughput_tps": c.throughput_tps,
                        "token_p99_ms": c.token_p99_ms,
                        "rps": c.rps,
                    }
                )
            summary["models"][r.model_name]["scenarios"].append(scenario_data)

    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"Summary saved: {summary_path}")

    # Print final table
    print("\n" + "=" * 60)
    print("  BENCHMARK COMPLETE")
    print("=" * 60)
    for r in all_results:
        print(f"\n  {r.model_name} ({r.model_size_mb} MB) [{r.mode}]")
        for sr in r.scenario_results:
            print(f"\n  Scenario: {sr.scenario_name} (weight={sr.weight}%)")
            print(
                f"  {'Conc':<6} {'OK%':<7} {'TTFT':<8} {'TTFT_P95':<10} "
                f"{'TPOT':<7} {'TPS':<8} {'RPS':<7} {'P99_Tok':<8}"
            )
            print(f"  {'-' * 60}")
            for c in sr.concurrency_results:
                ok_pct = (
                    round(c.success_count / c.total_count * 100)
                    if c.total_count > 0
                    else 0
                )
                print(
                    f"  {c.concurrency:<6} {ok_pct}%{'':<3} "
                    f"{c.ttft_avg_ms:.0f}ms{'':<3} "
                    f"{c.ttft_p95_ms:.0f}ms{'':<4} "
                    f"{c.tpot_avg_ms:.0f}ms{'':<3} "
                    f"{c.throughput_tps:.0f}{'':<5}"
                    f"{c.rps:.1f}{'':<3}"
                    f"{c.token_p99_ms:.0f}ms"
                )

    print(f"\n  Next: python3 vllm_report.py")


if __name__ == "__main__":
    main()