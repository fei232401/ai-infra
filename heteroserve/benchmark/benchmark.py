#!/usr/bin/env python3
"""
HeteroServe 前缀复用 benchmark — 量化 LMCache 分层缓存的 KV 缓存复用价值

设计:
  生成 N 个超长共享前缀, 每个前缀查询两次 (cold/warm)
  测 TTFT (首 token 延迟), 对比无缓存 vs 有缓存

用法:
  python3 benchmark.py --url http://localhost:8000/v1 \
      --model /models/qwen2.5-3b-awq \
      --num-prefixes 10 --prefix-tokens 3000 \
      --queries-per-prefix 2
"""
import argparse, json, time, uuid
import urllib.request

import random, hashlib

def make_prefix(tokens: int, seed: int = 0) -> str:
    """生成指定 token 数的合成前缀, 不同 seed 内容互不相同 (用于撑爆 GPU KV 触发逐出)"""
    chunks = [
        "容器编排平台自动化地管理应用生命周期，包括部署、滚动更新、回滚和扩缩容。",
        "Kubernetes控制平面由API Server、调度器和控制器管理器组成，持续协调期望状态。",
        "数据平面工作节点运行kubelet和容器运行时，负责实际执行工作负载并上报状态。",
        "网络插件CNI负责Pod网络，Service通过标签选择器实现稳定的服务发现与负载均衡。",
        "存储抽象PersistentVolume与PersistentVolumeClaim解耦存储提供方与使用方。",
        "弹性伸缩HorizontalPodAutoscaler根据CPU利用率或自定义指标动态调整副本数。",
        "配置管理ConfigMap与Secret分别承载非敏感与敏感数据，支持热更新与挂载。",
        "安全模型基于RBAC角色权限控制，Namespace提供多租户隔离与资源配额管理。",
        "调度器考虑资源请求、亲和性、污点容忍等约束，将Pod绑定到最合适节点。",
        "可观测性三支柱Metrics、Logging、Tracing覆盖系统运行的完整信号链路。",
    ]
    rng = random.Random(seed)
    shuffled = chunks[:]
    rng.shuffle(shuffled)
    # 唯一标记 + 打乱顺序, 保证不同 seed 前缀完全不同
    base = f"[知识库-{seed}]" + "".join(shuffled)
    # 实测: 5000 字符 ≈ 2444 tokens (Qwen2.5 tokenizer, ~0.489 token/字符)
    target_chars = int(tokens * 2.045)
    while len(base) < target_chars:
        rng.shuffle(shuffled)
        base += "".join(shuffled)
    return base[:target_chars]

def query_ttft(url, model, messages, max_tokens=20, timeout=180):
    """发送流式请求, 返回 (ttft_s, total_s)"""
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": True,
    }
    req = urllib.request.Request(
        f"{url}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    t0 = time.time()
    ttft = None
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for line in resp:
            line = line.decode().strip()
            if line.startswith("data:") and line != "data: [DONE]":
                if ttft is None:
                    ttft = time.time() - t0
    total = time.time() - t0
    return ttft, total

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000/v1")
    ap.add_argument("--model", default="/models/qwen2.5-3b-awq")
    ap.add_argument("--num-prefixes", type=int, default=10)
    ap.add_argument("--prefix-tokens", type=int, default=3000)
    ap.add_argument("--queries-per-prefix", type=int, default=2)
    args = ap.parse_args()

    print(f"=== 前缀复用 benchmark: {args.num_prefixes} 前缀 × {args.prefix_tokens} tokens × {args.queries_per_prefix} 查询 ===")
    results = []
    # 第一轮: 全部前缀 cold 查询, 撑满 GPU KV (触发旧前缀逐出)
    prefixes = [make_prefix(args.prefix_tokens, seed=p) for p in range(args.num_prefixes)]
    for p in range(args.num_prefixes):
        q_txt = f"问题{p}-cold: 什么是滚动更新策略？"
        ttft, total = query_ttft(args.url, args.model,
                                 [{"role": "user", "content": prefixes[p] + q_txt}])
        results.append({"prefix": p, "query": 0, "tag": "cold", "ttft_s": ttft, "total_s": total})
        print(f"  [cold ] prefix={p} TTFT={ttft:.3f}s total={total:.3f}s")
        time.sleep(1)
    # 第二轮: 反向顺序 warm 查询 — 第一轮的 prefix=0 已被后续前缀逐出, 验证 LMCache 能否恢复
    for p in range(args.num_prefixes - 1, -1, -1):
        q_txt = f"问题{p}-warm: 什么是Deployment控制器？"
        ttft, total = query_ttft(args.url, args.model,
                                 [{"role": "user", "content": prefixes[p] + q_txt}])
        results.append({"prefix": p, "query": 1, "tag": "warm", "ttft_s": ttft, "total_s": total})
        print(f"  [warm ] prefix={p} TTFT={ttft:.3f}s total={total:.3f}s")
        time.sleep(1)

    # 汇总
    cold = [r["ttft_s"] for r in results if r["tag"] == "cold"]
    warm = [r["ttft_s"] for r in results if r["tag"] == "warm"]
    if cold and warm:
        avg_cold = sum(cold)/len(cold)
        avg_warm = sum(warm)/len(warm)
        print(f"\n=== 汇总 ===")
        print(f"  cold TTFT 均值: {avg_cold:.3f}s  ({len(cold)} 次)")
        print(f"  warm TTFT 均值: {avg_warm:.3f}s  ({len(warm)} 次)")
        print(f"  缓存提速: {(1 - avg_warm/avg_cold)*100:.1f}%")
    # 存结果
    out = f"benchmark_results_{uuid.uuid4().hex[:8]}.json"
    with open(out, "w") as f:
        json.dump(results, f, indent=2)
    print(f"  结果已存: {out}")

if __name__ == "__main__":
    main()
