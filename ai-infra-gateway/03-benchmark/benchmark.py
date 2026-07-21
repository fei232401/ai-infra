#!/usr/bin/env python3
"""
阶段3：LLM 极限压测脚本
指标采集：TTFT(首字延迟)、TPOT(每token延迟)、Throughput(吞吐)
GPU监控：通过 pynvml 采集显存/温度/利用率
"""
import asyncio
import time
import json
import sys
import os
from dataclasses import dataclass, field
from typing import Optional

import aiohttp
import pynvml

# ============ 配置 ============
API_KEY = "sk-infra-gateway-dev-key-2026"
GATEWAY_URL = "http://localhost:8000"
OLLAMA_URL = "http://localhost:11434"

# 压测参数
CONCURRENCY_LEVELS = [1, 2, 4, 8, 16, 20]  # 渐进式并发
REQUESTS_PER_LEVEL = 10
PROMPT = "Explain GPU memory hierarchy and KV cache in one paragraph. Be technical and concise."
TIMEOUT = 120

# ============ 数据结构 ============
@dataclass
class RequestMetrics:
    """单次请求的指标"""
    concurrency: int
    request_id: int
    ttft_ms: float = 0.0         # Time To First Token (ms)
    tpot_ms: float = 0.0         # Time Per Output Token (ms)
    total_time_s: float = 0.0    # 总时间(秒)
    input_tokens: int = 0
    output_tokens: int = 0
    throughput_tps: float = 0.0  # tokens/s
    status: str = "unknown"
    error: str = ""

@dataclass
class GpuSnapshot:
    """GPU 快照"""
    timestamp: float
    memory_used_mb: int
    memory_free_mb: int
    memory_total_mb: int
    utilization_pct: int
    temperature_c: int

# ============ GPU 监控 ============
class GpuMonitor:
    def __init__(self):
        try:
            pynvml.nvmlInit()
            self.handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            self.available = True
        except:
            self.available = False
            print("[WARN] pynvml 不可用，GPU 监控关闭")

    def snapshot(self) -> Optional[GpuSnapshot]:
        if not self.available:
            return None
        try:
            mem = pynvml.nvmlDeviceGetMemoryInfo(self.handle)
            util = pynvml.nvmlDeviceGetUtilizationRates(self.handle)
            temp = pynvml.nvmlDeviceGetTemperature(self.handle, pynvml.NVML_TEMPERATURE_GPU)
            return GpuSnapshot(
                timestamp=time.time(),
                memory_used_mb=mem.used // (1024*1024),
                memory_free_mb=mem.free // (1024*1024),
                memory_total_mb=mem.total // (1024*1024),
                utilization_pct=util.gpu,
                temperature_c=temp,
            )
        except:
            return None

    def close(self):
        if self.available:
            pynvml.nvmlShutdown()

# ============ 压测核心 ============
async def single_request(session: aiohttp.ClientSession, concurrency: int, req_id: int) -> RequestMetrics:
    """发起一次流式请求并采集 TTFT/TPOT"""
    metrics = RequestMetrics(concurrency=concurrency, request_id=req_id)
    start_time = time.monotonic()
    first_token_time = None
    token_count = 0

    payload = {
        "model": "qwen2.5:1.5b",
        "prompt": PROMPT,
        "stream": True,
        "options": {"num_predict": 100}
    }
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with session.post(
            f"{GATEWAY_URL}/api/chat/stream",
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=TIMEOUT),
        ) as resp:
            if resp.status != 200:
                metrics.status = f"HTTP_{resp.status}"
                metrics.error = await resp.text()
                return metrics

            async for line in resp.content:
                line = line.decode("utf-8").strip()
                if not line or not line.startswith("data:"):
                    continue

                data_str = line[5:].strip()
                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                # 记录首字时间
                if first_token_time is None and chunk.get("response"):
                    first_token_time = time.monotonic()

                if chunk.get("response"):
                    token_count += 1

                if chunk.get("done"):
                    break

        end_time = time.monotonic()
        metrics.total_time_s = end_time - start_time
        if first_token_time:
            metrics.ttft_ms = (first_token_time - start_time) * 1000
        if token_count > 0:
            if first_token_time:
                generation_time = end_time - first_token_time
                metrics.tpot_ms = (generation_time / token_count) * 1000
            metrics.throughput_tps = token_count / metrics.total_time_s if metrics.total_time_s > 0 else 0
        metrics.output_tokens = token_count
        metrics.status = "OK"

    except asyncio.TimeoutError:
        metrics.status = "TIMEOUT"
        metrics.error = f"超时 ({TIMEOUT}s)"
    except aiohttp.ClientError as e:
        metrics.status = "ERROR"
        metrics.error = str(e)
    except Exception as e:
        metrics.status = "ERROR"
        metrics.error = str(e)

    return metrics

async def run_benchmark_level(concurrency: int) -> list[RequestMetrics]:
    """对一个并发级别执行压测"""
    print(f"\n{'='*50}")
    print(f"  并发级别: {concurrency}")
    print(f"{'='*50}")

    connector = aiohttp.TCPConnector(limit=concurrency + 5)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [
            single_request(session, concurrency, i)
            for i in range(REQUESTS_PER_LEVEL)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    metrics_list = []
    for r in results:
        if isinstance(r, RequestMetrics):
            metrics_list.append(r)
        else:
            print(f"  [EXCEPTION] {r}")

    return metrics_list

def print_report(concurrency: int, metrics: list[RequestMetrics]):
    """输出每个并发级别的报告"""
    ok_metrics = [m for m in metrics if m.status == "OK"]
    total = len(metrics)

    if not ok_metrics:
        print(f"  全部失败 ({total}/{total} failed)")
        return

    ttft_list = [m.ttft_ms for m in ok_metrics if m.ttft_ms > 0]
    tpot_list = [m.tpot_ms for m in ok_metrics if m.tpot_ms > 0]
    tps_list = [m.throughput_tps for m in ok_metrics if m.throughput_tps > 0]

    avg_ttft = sum(ttft_list) / len(ttft_list) if ttft_list else 0
    p95_ttft = sorted(ttft_list)[int(len(ttft_list)*0.95)] if len(ttft_list) >= 20 else max(ttft_list) if ttft_list else 0
    avg_tpot = sum(tpot_list) / len(tpot_list) if tpot_list else 0
    total_tps = sum(tps_list)

    print(f"  成功率: {len(ok_metrics)}/{total} ({len(ok_metrics)/total*100:.0f}%)")
    print(f"  TTFT avg: {avg_ttft:.1f} ms | P95: {p95_ttft:.1f} ms")
    print(f"  TPOT avg: {avg_tpot:.1f} ms")
    print(f"  总吞吐: {total_tps:.1f} tokens/s")
    print(f"  失败: {total - len(ok_metrics)} 个 (HTTP 429/超时等)")

async def main():
    print("=" * 60)
    print("  AI Infra LLM 推理压测")
    print(f"  网关: {GATEWAY_URL}")
    print(f"  并发梯度: {CONCURRENCY_LEVELS}")
    print(f"  每级请求数: {REQUESTS_PER_LEVEL}")
    print("=" * 60)

    gpu = GpuMonitor()

    all_results = {}
    gpu_snapshots = []

    # 基线 GPU 快照
    snap = gpu.snapshot()
    if snap:
        gpu_snapshots.append(snap)
        print(f"\n[GPU基线] 显存:{snap.memory_used_mb}MB/{snap.memory_total_mb}MB 温度:{snap.temperature_c}°C")

    for concurrency in CONCURRENCY_LEVELS:
        # 压测前 GPU 快照
        snap = gpu.snapshot()
        if snap:
            gpu_snapshots.append(snap)

        results = await run_benchmark_level(concurrency)
        all_results[concurrency] = results
        print_report(concurrency, results)

        # 压测后 GPU 快照
        snap = gpu.snapshot()
        if snap:
            gpu_snapshots.append(snap)
            print(f"  [GPU] 显存:{snap.memory_used_mb}MB 利用率:{snap.utilization_pct}% 温度:{snap.temperature_c}°C")

        # 间隔冷却
        await asyncio.sleep(2)

    # ========== 汇总报告 ==========
    print("\n" + "=" * 60)
    print("  压测汇总报告")
    print("=" * 60)
    print(f"{'并发':>6} | {'TTFT_avg':>9} | {'TTFT_P95':>9} | {'TPOT_avg':>9} | {'总吞吐':>8} | {'GPU%':>5} | {'显存MB':>7} | {'成功率':>6}")
    print("-" * 80)

    for i, concurrency in enumerate(CONCURRENCY_LEVELS):
        metrics = all_results.get(concurrency, [])
        ok_m = [m for m in metrics if m.status == "OK"]

        ttft_avg = sum(m.ttft_ms for m in ok_m if m.ttft_ms > 0) / max(len([m for m in ok_m if m.ttft_ms > 0]), 1)
        ttft_p95 = sorted([m.ttft_ms for m in ok_m if m.ttft_ms > 0])[int(len([m for m in ok_m if m.ttft_ms > 0])*0.95)] if len([m for m in ok_m if m.ttft_ms > 0]) >= 20 else 0
        tpot_avg = sum(m.tpot_ms for m in ok_m if m.tpot_ms > 0) / max(len([m for m in ok_m if m.tpot_ms > 0]), 1)
        total_tps = sum(m.throughput_tps for m in ok_m)
        success_rate = len(ok_m) / max(len(metrics), 1) * 100

        # GPU 数据
        gpu_idx = i * 2 + 1  # 每个级别前后的快照
        if gpu_idx < len(gpu_snapshots):
            gs = gpu_snapshots[gpu_idx]
            gpu_util = gs.utilization_pct
            gpu_mem = gs.memory_used_mb
        else:
            gpu_util = 0
            gpu_mem = 0

        print(f"{concurrency:>6} | {ttft_avg:>8.0f}ms | {ttft_p95:>8.0f}ms | {tpot_avg:>8.0f}ms | {total_tps:>7.0f}t/s | {gpu_util:>4}% | {gpu_mem:>6}MB | {success_rate:>5.0f}%")

    gpu.close()
    print("\n✅ 压测完成")

if __name__ == "__main__":
    asyncio.run(main())