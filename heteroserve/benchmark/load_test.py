#!/usr/bin/env python3
"""
HeteroServe 并发吞吐压测 — 调参矩阵 (H2) 的测量器

测当前 vLLM 配置下的稳态能力:
  - 吞吐: requests/s, output tokens/s
  - 延迟: TTFT P50/P95/P99, total P50/P95/P99
  - 错误: 失败请求数 (超时/HTTP错误)

负载: 共享前缀 + 变化尾巴 (与前缀复用 benchmark 同构, 模拟系统提示场景)

用法:
  python3 load_test.py --url http://localhost:8000/v1 \
      --model /models/qwen2.5-3b-awq \
      --concurrency 8 --requests 40 --prefix-tokens 1000 --max-tokens 32
"""
import argparse, asyncio, json, time, random, hashlib
import aiohttp
from benchmark import make_prefix

async def query_ttft(session, url, model, prompt, max_tokens, sem):
    """发送单个流式请求, 返回 (ttft_s, total_s) 或 None(失败)"""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "stream": True,
    }
    async with sem:
        t0 = time.time()
        ttft = None
        try:
            async with session.post(f"{url}/chat/completions", json=payload, timeout=aiohttp.ClientTimeout(total=180)) as resp:
                if resp.status != 200:
                    return None
                async for line in resp.content:
                    line = line.decode().strip()
                    if line.startswith("data:") and line != "data: [DONE]":
                        if ttft is None:
                            ttft = time.time() - t0
                total = time.time() - t0
            return (ttft, total)
        except Exception:
            return None

async def run(url, model, concurrency, requests, prefix_tokens, max_tokens):
    sem = asyncio.Semaphore(concurrency)
    conn = aiohttp.TCPConnector(limit=concurrency)
    t_start = time.time()
    async with aiohttp.ClientSession(connector=conn) as session:
        # 生成 requests 个互异请求: 共享前缀 + 变化问题
        prefix = make_prefix(prefix_tokens, seed=42)  # 同一前缀 = 共享 KV
        prompts = [prefix + f"问题{i}: 什么是滚动更新策略？" for i in range(requests)]
        tasks = [asyncio.create_task(query_ttft(session, url, model, p, max_tokens, sem)) for p in prompts]
        results = await asyncio.gather(*tasks)
    wall = time.time() - t_start

    ok = [r for r in results if r is not None]
    fails = len(results) - len(ok)
    if not ok:
        return {"requests": requests, "concurrency": concurrency, "failed": fails,
                "error": "all requests failed", "qps": 0.0, "tok_s": 0.0}

    ttfts = sorted(r[0] for r in ok)
    totals = sorted(r[1] for r in ok)
    elapsed = sum(r[1] for r in ok) / len(ok)  # 平均请求时长
    qps = len(ok) / max(wall, 1e-6)
    tok_s = len(ok) * max_tokens / max(wall, 1e-6)

    def pct(xs, p):
        i = min(int(len(xs) * p), len(xs) - 1)
        return round(xs[i], 4)

    return {
        "requests": requests, "concurrency": concurrency, "ok": len(ok), "failed": fails,
        "qps": round(qps, 2), "output_tokens_per_s": round(tok_s, 1),
        "ttft_p50": pct(ttfts, 0.50), "ttft_p95": pct(ttfts, 0.95), "ttft_p99": pct(ttfts, 0.99),
        "total_p50": pct(totals, 0.50), "total_p95": pct(totals, 0.95),
        "avg_req_s": round(elapsed, 3),
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000/v1")
    ap.add_argument("--model", default="/models/qwen2.5-3b-awq")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--requests", type=int, default=40)
    ap.add_argument("--prefix-tokens", type=int, default=1000)
    ap.add_argument("--max-tokens", type=int, default=32)
    ap.add_argument("--out", default=None, help="JSON 输出路径")
    args = ap.parse_args()

    print(f"=== 压测: 并发{args.concurrency} × {args.requests}请求, 前缀{args.prefix_tokens}tokens, 输出{args.max_tokens}tokens ===")
    result = asyncio.run(run(args.url, args.model, args.concurrency, args.requests,
                             args.prefix_tokens, args.max_tokens))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if args.out:
        with open(args.out, "w") as f:
            json.dump(result, f, indent=2)
        print(f"结果已存: {args.out}")

if __name__ == "__main__":
    main()
