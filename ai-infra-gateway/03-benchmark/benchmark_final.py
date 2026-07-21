#!/usr/bin/env python3
"""
阶段3：LLM 推理全维度压测 + 性能基准评估
"""
import asyncio, time, json, statistics
from dataclasses import dataclass, field

import aiohttp

API_KEY = "sk-infra-gateway-dev-key-2026"
GATEWAY_URL = "http://localhost:8000"
MODELS = ["qwen2.5:0.5b", "qwen2.5:1.5b"]
CONCURRENCY_LEVELS = [1, 2, 4, 8]
REQUESTS_PER_LEVEL = 8
PROMPT = "Explain GPU memory hierarchy and KV cache in detail."
TIMEOUT = 120

@dataclass
class RequestResult:
    model: str
    concurrency: int
    status: str = "unknown"
    ttft_ms: float = 0.0
    total_time_s: float = 0.0
    output_tokens: int = 0
    total_throughput_tps: float = 0.0
    tpot_ms: float = 0.0
    token_latencies: list = field(default_factory=list)
    gpu_mem_used_mb: int = 0
    error: str = ""

async def single_request(session, model, concurrency, req_id):
    result = RequestResult(model=model, concurrency=concurrency)
    start = time.monotonic()
    first_token_time = None
    token_count = 0
    token_times = []
    payload = {"model": model, "prompt": PROMPT, "stream": True, "options": {"num_predict": 100}}
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    try:
        async with session.post(f"{GATEWAY_URL}/api/chat/stream", json=payload, headers=headers,
                                timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as resp:
            if resp.status != 200:
                result.status = f"HTTP_{resp.status}"
                try:
                    result.error = (await resp.text())[:100]
                except:
                    result.error = "read error"
                return result
            last_token_time = start
            async for line in resp.content:
                line = line.decode("utf-8", errors="ignore").strip()
                if not line or not line.startswith("data:"):
                    continue
                try:
                    chunk = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                if chunk.get("response"):
                    now = time.monotonic()
                    if first_token_time is None:
                        first_token_time = now
                        result.ttft_ms = (now - start) * 1000
                    token_count += 1
                    token_times.append((now - last_token_time) * 1000)
                    last_token_time = now
                if chunk.get("done"):
                    break
        end = time.monotonic()
        result.total_time_s = end - start
        result.output_tokens = token_count
        result.token_latencies = token_times
        if token_count > 0 and result.total_time_s > 0:
            result.total_throughput_tps = token_count / result.total_time_s
            gen_time = end - first_token_time if first_token_time else 0
            result.tpot_ms = (gen_time / token_count) * 1000 if gen_time > 0 else 0
        result.status = "OK"
    except asyncio.TimeoutError:
        result.status = "TIMEOUT"
    except Exception as e:
        result.status = "ERROR"
        result.error = str(e)[:100]
    return result

async def run_level(model, concurrency):
    connector = aiohttp.TCPConnector(limit=concurrency + 5)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [single_request(session, model, concurrency, i) for i in range(REQUESTS_PER_LEVEL)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    return [r for r in results if isinstance(r, RequestResult)]

def print_report(all_results):
    print("\n" + "=" * 70)
    print("  LLM Inference Benchmark Report")
    print("=" * 70)
    print(f"  Model: {MODELS[0]} | Concurrency: {CONCURRENCY_LEVELS}")
    print("=" * 70)

    for model in MODELS:
        print(f"\n{'='*70}")
        print(f"  Model: {model}")
        print(f"  {'Conc':>4} {'OK%':>5} {'TTFT_avg':>9} {'TTFT_P95':>9} {'TPOT':>8} {'Throughput':>10} {'Token_P99':>10}")
        print(f"  {'-'*64}")

        for concurrency in CONCURRENCY_LEVELS:
            key = (model, concurrency)
            results = all_results.get(key, [])
            ok = [r for r in results if r.status == "OK"]
            total = len(results)
            if not ok:
                print(f"  {concurrency:>4} {0:>5}% {'N/A':>9} {'N/A':>9} {'N/A':>8} {'N/A':>10} {'N/A':>10}")
                continue

            ttfts = [r.ttft_ms for r in ok if r.ttft_ms > 0]
            tpots = [r.tpot_ms for r in ok if r.tpot_ms > 0]
            tps_list = [r.total_throughput_tps for r in ok]
            all_lat = [lat for r in ok for lat in r.token_latencies]

            avg_ttft = statistics.mean(ttfts) if ttfts else 0
            p95_ttft = sorted(ttfts)[int(len(ttfts)*0.95)] if len(ttfts) >= 20 else (max(ttfts) if ttfts else 0)
            avg_tpot = statistics.mean(tpots) if tpots else 0
            total_tps = sum(tps_list)
            p99_token = sorted(all_lat)[int(len(all_lat)*0.99)] if len(all_lat) >= 100 else (max(all_lat) if all_lat else 0)

            print(f"  {concurrency:>4} {len(ok)/total*100:>4.0f}% {avg_ttft:>8.0f}ms {p95_ttft:>8.0f}ms {avg_tpot:>7.0f}ms {total_tps:>9.0f}t/s {p99_token:>9.0f}ms")

        # Token latency distribution
        print(f"\n  Token Latency Distribution (concurrency=4):")
        key = (model, 4)
        results = all_results.get(key, [])
        ok = [r for r in results if r.status == "OK"]
        all_lat = sorted([lat for r in ok for lat in r.token_latencies])
        if all_lat:
            print(f"    Samples: {len(all_lat)} | Mean: {statistics.mean(all_lat):.1f}ms | Median: {statistics.median(all_lat):.1f}ms")
            print(f"    P50: {sorted(all_lat)[int(len(all_lat)*0.50)]:.1f}ms | P90: {sorted(all_lat)[int(len(all_lat)*0.90)]:.1f}ms")
            print(f"    P95: {sorted(all_lat)[int(len(all_lat)*0.95)]:.1f}ms | P99: {sorted(all_lat)[int(len(all_lat)*0.99)]:.1f}ms")

        # Throughput inflection
        print(f"\n  Throughput Inflection:")
        prev_tps = 0
        for c in CONCURRENCY_LEVELS:
            key = (model, c)
            results = all_results.get(key, [])
            ok_r = [r for r in results if r.status == "OK"]
            tps = sum(r.total_throughput_tps for r in ok_r) if ok_r else 0
            gain = (tps - prev_tps) / max(prev_tps, 1) * 100 if prev_tps > 0 else 0
            marker = " <-- inflection" if 0 < gain < 10 else ""
            print(f"    C{c}: {tps:.0f} t/s ({gain:+.0f}%){marker}")
            prev_tps = tps

    print(f"\n{'='*70}")
    print("  Benchmark complete.")
    print("=" * 70)

async def main():
    all_results = {}
    for model in MODELS:
        for concurrency in CONCURRENCY_LEVELS:
            print(f"  [Test] {model} @ concurrency={concurrency} ...")
            results = await run_level(model, concurrency)
            all_results[(model, concurrency)] = results
    print_report(all_results)

if __name__ == "__main__":
    asyncio.run(main())