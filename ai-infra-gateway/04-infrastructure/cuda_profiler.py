#!/usr/bin/env python3
"""
CUDA Precision Profiler — PyTorch-based GPU micro-benchmarking toolkit.

Measures hardware-level metrics that pynvml cannot access:
  - GPU kernel execution latency (ns precision via CUDA Events)
  - Memory bandwidth (GB/s) — copy and compute
  - Peak FLOPS (TFLOPS) — mixed-precision GEMM
  - Attention micro-benchmark — Q·K^T + softmax + ·V
  - vLLM inference overhead profiling

Requirements: torch (PyTorch with CUDA), CUDA Toolkit >= 12.x
Usage: python cuda_profiler.py [--bench all|memory|compute|attention|vllm]
"""

import argparse
import json
import math
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

try:
    import torch
    import torch.cuda
    HAS_TORCH = torch.cuda.is_available()
except ImportError:
    HAS_TORCH = False
    print("⚠️  PyTorch not installed. Install: pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121")


# ============================================================================
# Data Model
# ============================================================================

@dataclass
class GPUMetrics:
    """Aggregated GPU performance metrics."""
    device_name: str = ""
    compute_capability: str = ""
    total_vram_gb: float = 0.0
    free_vram_gb: float = 0.0

    # Memory bandwidth (GB/s)
    mem_h2d_bw: float = 0.0          # Host → Device
    mem_d2h_bw: float = 0.0          # Device → Host
    mem_device_bw: float = 0.0        # Device-internal copy

    # Compute (TFLOPS)
    fp16_tflops: float = 0.0
    fp32_tflops: float = 0.0

    # Attention micro-bench
    attention_fwd_ms: float = 0.0     # Single forward pass
    attention_bwd_ms: float = 0.0     # Forward + backward

    # vLLM / Inference proxy overhead
    inference_ttft_ms: float = 0.0     # TTFT via vLLM API
    inference_tpot_ms: float = 0.0     # TPOT via vLLM API

    raw: Dict = field(default_factory=dict)


# ============================================================================
# CUDA Event Utilities
# ============================================================================

def cuda_event_timer(fn, warmup=3, repeat=10):
    """Time a CUDA operation using CUDA Events (ns precision)."""
    if not HAS_TORCH:
        return 0.0, 0.0

    # Warmup
    for _ in range(warmup):
        fn()

    torch.cuda.synchronize()
    start = torch.cuda.Event(enable_timing=True)
    end = torch.cuda.Event(enable_timing=True)

    start.record()
    for _ in range(repeat):
        fn()
    end.record()
    torch.cuda.synchronize()

    total_ms = start.elapsed_time(end)
    avg_ms = total_ms / repeat
    return avg_ms


# ============================================================================
# Benchmarks
# ============================================================================

def bench_memory_bandwidth() -> Dict:
    """Measure H2D, D2H, and device-internal memory bandwidth."""
    size = 256 * 1024 * 1024  # 256 MB
    nbytes = size * 4  # float32 = 4 bytes
    repeats = 5

    results = {}
    try:
        # Host → Device
        data_cpu = torch.randn(size, dtype=torch.float32, device="cpu")
        def _h2d():
            data_cpu.to("cuda", non_blocking=True)
        ms = cuda_event_timer(_h2d, warmup=2, repeat=repeats)
        results["h2d_gb_s"] = round(nbytes / (ms / 1000) / 1e9, 2)

        # Device → Host
        data_gpu = torch.randn(size, dtype=torch.float32, device="cuda")
        buf_cpu = torch.empty(size, dtype=torch.float32, device="cpu", pin_memory=True)
        def _d2h():
            buf_cpu.copy_(data_gpu, non_blocking=True)
        ms = cuda_event_timer(_d2h, warmup=2, repeat=repeats)
        results["d2h_gb_s"] = round(nbytes / (ms / 1000) / 1e9, 2)

        # Device-internal copy
        src = torch.randn(size, dtype=torch.float32, device="cuda")
        dst = torch.empty(size, dtype=torch.float32, device="cuda")
        def _internal():
            dst.copy_(src)
        ms = cuda_event_timer(_internal, warmup=2, repeat=repeats)
        results["device_gb_s"] = round(nbytes / (ms / 1000) / 1e9, 2)

    except Exception as e:
        results["error"] = str(e)

    return results


def bench_compute_flops() -> Dict:
    """Measure peak FLOPS via large GEMM (matrix multiply)."""
    N = 8192   # 8K × 8K matrix
    repeats = 5
    results = {}

    try:
        # FP16 GEMM — 2 * N^3 FLOPs per multiply
        flops_per_mul = 2 * N**3

        a_fp16 = torch.randn(N, N, dtype=torch.float16, device="cuda")
        b_fp16 = torch.randn(N, N, dtype=torch.float16, device="cuda")
        def _fp16_gemm():
            torch.matmul(a_fp16, b_fp16)
        ms = cuda_event_timer(_fp16_gemm, warmup=3, repeat=repeats)
        tflops = flops_per_mul / (ms / 1000) / 1e12
        results["fp16_tflops"] = round(tflops, 3)
        results["fp16_gemm_ms"] = round(ms, 3)

        # FP32 GEMM
        a_fp32 = a_fp16.float()
        b_fp32 = b_fp16.float()
        def _fp32_gemm():
            torch.matmul(a_fp32, b_fp32)
        ms = cuda_event_timer(_fp32_gemm, warmup=3, repeat=repeats)
        tflops = flops_per_mul / (ms / 1000) / 1e12
        results["fp32_tflops"] = round(tflops, 3)
        results["fp32_gemm_ms"] = round(ms, 3)

    except Exception as e:
        results["error"] = str(e)

    return results


def bench_attention_micro() -> Dict:
    """Micro-benchmark scaled dot-product attention (Q·K^T + softmax + ·V).

    Simulates a single Transformer decoder layer with:
      bs=1, n_heads=32, head_dim=128, seq_len=2048
    """
    bs, n_heads, head_dim, seq_len = 1, 32, 128, 2048
    d_model = n_heads * head_dim  # 4096
    results = {}

    try:
        q = torch.randn(bs, n_heads, seq_len, head_dim, dtype=torch.float16, device="cuda")
        k = torch.randn(bs, n_heads, seq_len, head_dim, dtype=torch.float16, device="cuda")
        v = torch.randn(bs, n_heads, seq_len, head_dim, dtype=torch.float16, device="cuda")

        def _attention():
            # scaled dot-product attention
            scale = 1.0 / math.sqrt(head_dim)
            attn_weights = torch.matmul(q, k.transpose(-2, -1)) * scale
            attn_weights = torch.softmax(attn_weights, dim=-1)
            out = torch.matmul(attn_weights, v)
            return out

        ms_fwd = cuda_event_timer(lambda: _attention(), warmup=3, repeat=10)
        results["attention_fwd_ms"] = round(ms_fwd, 4)
        results["flops_per_attention"] = f"{4 * bs * n_heads * seq_len * seq_len * head_dim / 1e9:.1f} GFLOPs"

        # Forward + Backward
        q.requires_grad_(True)
        k.requires_grad_(True)
        v.requires_grad_(True)
        def _fwd_bwd():
            out = _attention()
            loss = out.sum()
            loss.backward()

        ms_bwd = cuda_event_timer(_fwd_bwd, warmup=2, repeat=5)
        results["attention_fwd_bwd_ms"] = round(ms_bwd, 4)

    except Exception as e:
        results["error"] = str(e)

    return results


def bench_vllm_overhead(vllm_url: str = "http://127.0.0.1:8000") -> Dict:
    """Measure vLLM inference TTFT and TPOT via HTTP API."""
    import urllib.request
    import urllib.error

    results = {}
    model_name = None

    # Discover model
    try:
        req = urllib.request.Request(f"{vllm_url}/v1/models")
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read())
        if data.get("data"):
            model_name = data["data"][0]["id"]
            results["model"] = model_name.split("/")[-1]
    except Exception as e:
        results["model_error"] = str(e)
        return results

    if not model_name:
        results["error"] = "No model found"
        return results

    # Measure TTFT + TPOT (non-streaming)
    payload = json.dumps({
        "model": model_name,
        "messages": [{"role": "user", "content": "Explain GPU computing in one sentence."}],
        "max_tokens": 64,
        "temperature": 0.0,
    }).encode("utf-8")

    try:
        t0 = time.perf_counter()
        req = urllib.request.Request(
            f"{vllm_url}/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=60)
        data = json.loads(resp.read())
        total_ms = (time.perf_counter() - t0) * 1000

        if "choices" in data:
            choice = data["choices"][0]
            completion_tokens = choice.get("message", {}).get("content", "")
            token_count = data.get("usage", {}).get("completion_tokens", 0)

            results["total_ms"] = round(total_ms, 2)
            results["completion_tokens"] = token_count
            results["ttft_ms"] = round(total_ms, 2)  # non-streaming: total ≈ TTFT-ish
            results["tpot_ms"] = round(total_ms / max(token_count, 1), 2)

            # Also check vLLM health
            hreq = urllib.request.Request(f"{vllm_url}/health")
            hresp = urllib.request.urlopen(hreq, timeout=3)
            results["health"] = "ok" if hresp.status == 200 else f"status_{hresp.status}"

    except urllib.error.URLError as e:
        results["api_error"] = f"Connection failed: {e.reason}"
    except Exception as e:
        results["api_error"] = str(e)

    return results


# ============================================================================
# Main
# ============================================================================

def get_gpu_info() -> Dict:
    """Query GPU hardware info via PyTorch."""
    if not HAS_TORCH:
        return {"error": "PyTorch CUDA not available"}

    props = torch.cuda.get_device_properties(0)
    mem = torch.cuda.mem_get_info()
    free_mb, total_mb = mem[0] / 1024**2, mem[1] / 1024**2

    return {
        "device_name": props.name,
        "compute_capability": f"{props.major}.{props.minor}",
        "cuda_cores": props.multi_processor_count * 128,  # approximate for Ampere/Ada
        "total_vram_gb": round(total_mb / 1024, 1),
        "free_vram_gb": round(free_mb / 1024, 1),
        "used_vram_gb": round((total_mb - free_mb) / 1024, 1),
        "max_clock_mhz": props.clock_rate / 1000,
        "memory_clock_mhz": props.memory_clock_rate / 1000,
        "memory_bus_width_bits": props.memory_bus_width if hasattr(props, 'memory_bus_width') else "unknown",
    }


def run_all_benchmarks() -> GPUMetrics:
    """Run all benchmarks and return aggregated results."""
    metrics = GPUMetrics()

    # GPU info
    gpu = get_gpu_info()
    metrics.device_name = gpu.get("device_name", "unknown")
    metrics.compute_capability = gpu.get("compute_capability", "?")
    metrics.total_vram_gb = gpu.get("total_vram_gb", 0)
    metrics.free_vram_gb = gpu.get("free_vram_gb", 0)
    metrics.raw["gpu_info"] = gpu

    print(f"\n{'='*60}")
    print(f"  CUDA Profiler — {metrics.device_name} ({metrics.compute_capability})")
    print(f"  VRAM: {metrics.used_vram_gb:.1f}GB used / {metrics.total_vram_gb:.1f}GB total")
    print(f"{'='*60}\n")

    # Memory bandwidth
    print("[1/4] Memory bandwidth...")
    mem = bench_memory_bandwidth()
    metrics.mem_h2d_bw = mem.get("h2d_gb_s", 0)
    metrics.mem_d2h_bw = mem.get("d2h_gb_s", 0)
    metrics.mem_device_bw = mem.get("device_gb_s", 0)
    metrics.raw["memory"] = mem
    print(f"  H2D: {metrics.mem_h2d_bw:.0f} GB/s  |  D2H: {metrics.mem_d2h_bw:.0f} GB/s  |  Device: {metrics.mem_device_bw:.0f} GB/s")

    # Compute FLOPS
    print("[2/4] Peak FLOPS...")
    comp = bench_compute_flops()
    metrics.fp16_tflops = comp.get("fp16_tflops", 0)
    metrics.fp32_tflops = comp.get("fp32_tflops", 0)
    metrics.raw["compute"] = comp
    print(f"  FP16: {metrics.fp16_tflops:.3f} TFLOPS  |  FP32: {metrics.fp32_tflops:.3f} TFLOPS")

    # Attention micro
    print("[3/4] Attention micro-benchmark...")
    attn = bench_attention_micro()
    metrics.attention_fwd_ms = attn.get("attention_fwd_ms", 0)
    metrics.attention_bwd_ms = attn.get("attention_fwd_bwd_ms", 0)
    metrics.raw["attention"] = attn
    print(f"  Attention Fwd: {metrics.attention_fwd_ms:.2f}ms  |  Fwd+Bwd: {metrics.attention_bwd_ms:.2f}ms")

    # vLLM overhead
    print("[4/4] vLLM inference overhead...")
    vllm = bench_vllm_overhead()
    metrics.inference_ttft_ms = vllm.get("ttft_ms", 0)
    metrics.inference_tpot_ms = vllm.get("tpot_ms", 0)
    metrics.raw["vllm"] = vllm
    if "model" in vllm:
        print(f"  Model: {vllm['model']}  |  TTFT: {metrics.inference_ttft_ms:.1f}ms  |  TPOT: {metrics.inference_tpot_ms:.1f}ms/token")
    elif "api_error" in vllm:
        print(f"  vLLM API: {vllm['api_error']}")

    return metrics


def print_summary_table(metrics: GPUMetrics):
    """Print a formatted summary table."""
    print(f"\n{'='*60}")
    print(f"  SUMMARY — {metrics.device_name}")
    print(f"{'='*60}")
    print(f"  Compute Capability     : {metrics.compute_capability}")
    print(f"  VRAM Total / Free      : {metrics.total_vram_gb:.0f} GB / {metrics.free_vram_gb:.0f} GB")
    print(f"  ---")
    if metrics.mem_device_bw:
        print(f"  Memory Bandwidth (Dev) : {metrics.mem_device_bw:.0f} GB/s")
    if metrics.fp16_tflops:
        print(f"  FP16 GEMM              : {metrics.fp16_tflops:.2f} TFLOPS")
    if metrics.fp32_tflops:
        print(f"  FP32 GEMM              : {metrics.fp32_tflops:.2f} TFLOPS")
    if metrics.attention_fwd_ms:
        print(f"  Attention (seq=2048)   : {metrics.attention_fwd_ms:.2f} ms fwd")
    if metrics.inference_ttft_ms:
        print(f"  ---")
        print(f"  vLLM TTFT (real)       : {metrics.inference_ttft_ms:.1f} ms")
        print(f"  vLLM TPOT (real)       : {metrics.inference_tpot_ms:.1f} ms/tok")
    print(f"{'='*60}\n")


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="CUDA Precision Profiler")
    parser.add_argument("--bench", choices=["all", "memory", "compute", "attention", "vllm"], default="all",
                        help="Which benchmark to run (default: all)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    if not HAS_TORCH:
        print(json.dumps({"error": "PyTorch CUDA not available. Install with: pip install torch"}) if args.json 
              else "ERROR: PyTorch CUDA not available.")
        sys.exit(1)

    if args.bench == "vllm":
        result = bench_vllm_overhead()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(json.dumps(result, indent=2))
        return

    if args.bench != "all":
        benches = {
            "memory": bench_memory_bandwidth,
            "compute": bench_compute_flops,
            "attention": bench_attention_micro,
        }
        result = benches[args.bench]()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(json.dumps(result, indent=2))
        return

    metrics = run_all_benchmarks()
    
    if args.json:
        output = {
            "gpu_info": metrics.raw["gpu_info"],
            "memory_bandwidth": metrics.raw["memory"],
            "compute_flops": metrics.raw["compute"],
            "attention": metrics.raw["attention"],
            "vllm_overhead": metrics.raw.get("vllm", {}),
        }
        print(json.dumps(output, indent=2))
    else:
        print_summary_table(metrics)


if __name__ == "__main__":
    main()