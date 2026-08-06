#!/usr/bin/env python3
"""
HeteroServe 调参矩阵 (H2) orchestrator — 自动扫配置空间

逐组合: patch vllm-3b deployment (gpu-memory-utilization, max-num-seqs)
       + lmcache-config configmap (max_local_cpu_size)
       → rollout restart → 等 pod Ready
       → kind=load 跑并发压测 / kind=prefix 跑前缀恢复专项
       → 采集 /metrics 缓存指标
       → 记录到 matrix_results.json
最后恢复 BASE 配置。

用法:
  python3 tuning_matrix.py                # 全矩阵
  python3 tuning_matrix.py --only A1      # 只跑某组合 (验证用)
"""
import argparse, json, subprocess, sys, time, os, glob, shutil

NS = "ai-platform"
DEP = "vllm-3b"
CM = "lmcache-config"
MODEL = "/models/qwen2.5-3b-awq"

# kind=load  → A/B 系列: 并发吞吐压测 (shared prefix)
# kind=prefix → C 系列: LMCache CPU 缓存扫描, 前缀恢复专项 (撑爆 GPU KV → 反向 warm)
MATRIX = [
    # (name, gpu_util, max_num_seqs, cpu_cache_gb, kind)
    ("A1", "0.7", "8",  "1.0", "load"),
    ("A2", "0.8", "8",  "1.0", "load"),
    ("A3", "0.9", "8",  "1.0", "load"),
    ("B1", "0.8", "4",  "1.0", "load"),
    ("B2", "0.8", "16", "1.0", "load"),
    ("B3", "0.8", "32", "1.0", "load"),
    ("C2", "0.8", "8",  "2.0", "prefix"),
    ("C3", "0.8", "8",  "4.0", "prefix"),
]
BASE = ("0.55", "4", "1.0")  # 恢复用

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results", "matrix_results.json")
LMCACHE_YAML_TMPL = """chunk_size: 256
local_cpu: True
max_local_cpu_size: {cpu_gb}
local_disk: "/tmp/lmcache_disk"
max_local_disk_size: 10.0
use_gds: False
"""

def kubectl(*args):
    r = subprocess.run(["kubectl"] + list(args), capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)} 失败: {r.stderr.strip()}")
    return r.stdout.strip()

def patch_deployment(gpu_util, seqs):
    args = [
        "--model", MODEL,
        "--gpu-memory-utilization", gpu_util,
        "--max-model-len", "5000",
        "--enforce-eager",
        "--max-num-seqs", seqs,
        "--kv-transfer-config", '{"kv_connector":"LMCacheConnectorV1","kv_role":"kv_both","kv_buffer_size":100000000}',
    ]
    json_patch = json.dumps([{"op": "replace", "path": "/spec/template/spec/containers/0/args", "value": args}])
    kubectl("patch", "deployment", DEP, "-n", NS, "--type=json", "-p", json_patch)

def patch_configmap(cpu_gb):
    data = LMCACHE_YAML_TMPL.format(cpu_gb=cpu_gb)
    kubectl("patch", "configmap", CM, "-n", NS, "--type=merge",
            "-p", json.dumps({"data": {"lmcache.yaml": data}}))

def wait_ready(timeout=420):
    """轮询 pod 直到 Ready, 返回 (ok, pod_name) — rollout status 在单GPU Recreate 场景不可靠"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = subprocess.run(
            ["kubectl", "get", "pods", "-n", NS, "-l", "app=vllm-3b",
             "-o", "jsonpath={.items[0].metadata.name} {.items[0].status.phase} {.items[0].status.containerStatuses[0].ready}"],
            capture_output=True, text=True)
        parts = r.stdout.split()
        if len(parts) == 3:
            name, phase, ready = parts
            if ready == "true":
                return True, name
            if phase in ("Failed", "Unknown"):
                return False, name
        time.sleep(5)
    return False, ""

def run_load_test(concurrency=8, requests=40, prefix_tokens=1000, max_tokens=32):
    """跑并发压测, 返回结果 dict"""
    r = subprocess.run([sys.executable, os.path.join(HERE, "load_test.py"),
                        "--url", "http://localhost:8000/v1", "--model", MODEL,
                        "--concurrency", str(concurrency), "--requests", str(requests),
                        "--prefix-tokens", str(prefix_tokens), "--max-tokens", str(max_tokens),
                        "--out", "/tmp/matrix_load.json"], capture_output=True, text=True)
    if r.returncode != 0:
        return {"error": r.stderr.strip()[-500:]}
    with open("/tmp/matrix_load.json") as f:
        return json.load(f)

def run_prefix_recovery():
    """跑前缀恢复专项 (撑爆 GPU KV → 反向 warm), 返回 {evicted_warm_ttfts, ...}"""
    r = subprocess.run([sys.executable, os.path.join(HERE, "benchmark.py"),
                        "--url", "http://localhost:8000/v1", "--model", MODEL,
                        "--num-prefixes", "20", "--prefix-tokens", "4000",
                        "--queries-per-prefix", "2"], capture_output=True, text=True)
    if r.returncode != 0:
        return {"error": r.stderr.strip()[-500:]}
    # 解析最新生成的 benchmark 结果: 找 warm 中"被逐出"的那批 (恢复 TTFT 明显高于未逐出的 0.04s)
    files = sorted(glob.glob(os.path.join(HERE, "results", "benchmark_results_*.json")), key=os.path.getmtime)
    if not files:
        return {"error": "无 benchmark 结果文件"}
    with open(files[-1]) as f:
        runs = json.load(f)
    warm = [r for r in runs if r["tag"] == "warm"]
    evicted = sorted(r["ttft_s"] for r in warm if r["ttft_s"] > 0.1)   # 恢复档 (vs GPU 命中 0.04s)
    fast = sorted(r["ttft_s"] for r in warm if r["ttft_s"] <= 0.1)     # GPU 缓存命中档
    return {
        "evicted_warm_ttfts": [round(x, 3) for x in evicted],
        "evicted_count": len(evicted),
        "evicted_ttft_median": round(evicted[len(evicted)//2], 3) if evicted else None,
        "gpu_hit_count": len(fast),
        "result_file": os.path.basename(files[-1]),
    }

def fetch_cache_metrics():
    """从 /metrics 采集缓存指标 (需 port-forward 在跑)"""
    import urllib.request
    try:
        body = urllib.request.urlopen("http://localhost:8000/metrics", timeout=10).read().decode()
    except Exception as e:
        return {"error": str(e)}
    def val(name):
        for line in body.splitlines():
            if line.startswith(name + "{"):
                return float(line.rsplit(" ", 1)[1])
        return None
    q = val("vllm:prefix_cache_queries_total")
    h = val("vllm:prefix_cache_hits_total")
    xt = val("vllm:prompt_tokens_by_source_total")
    # external_kv_transfer 行带 source 标签
    for line in body.splitlines():
        if line.startswith('vllm:prompt_tokens_by_source_total{') and "external_kv_transfer" in line:
            xt = float(line.rsplit(" ", 1)[1])
    return {
        "prefix_cache_queries": q, "prefix_cache_hits": h,
        "hit_ratio": round(h / q, 4) if q else None,
        "lmcache_kv_restored_tokens": xt,
    }

def apply_config(gpu_util, seqs, cpu_gb):
    patch_deployment(gpu_util, seqs)
    patch_configmap(cpu_gb)
    # 删旧 pod 强制按新 spec 重建 — 单 GPU 下 RollingUpdate/rollout 会死锁 (新pod等GPU, 旧pod不删)
    kubectl("delete", "pod", "-n", NS, "-l", "app=vllm-3b", "--ignore-not-found")
    ok, pod = wait_ready()
    return ok, pod

def collect_matrix(existing=None):
    data = existing if existing else {}
    for name, gpu, seqs, cpu, kind in MATRIX:
        print(f"\n{'='*60}\n[组合 {name}] gpu={gpu} seqs={seqs} cpu_cache={cpu}GB kind={kind}", flush=True)
        try:
            ok, pod = apply_config(gpu, seqs, cpu)
            if not ok:
                data[name] = {"status": "OOM/SKIP", "config": {"gpu_util": gpu, "seqs": seqs, "cpu_gb": cpu}}
                print(f"  ❌ {name}: pod 未就绪 (OOM?) — 跳过", flush=True)
                continue
            print(f"  ✅ pod 就绪: {pod}", flush=True)
            time.sleep(5)  # 等指标预热

            # port-forward
            pf = subprocess.Popen(["kubectl", "port-forward", "-n", NS,
                                   f"svc/vllm-3b-service", "8000:8000"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(4)
            try:
                if kind == "load":
                    result = run_load_test()
                    if "error" in result:
                        raise RuntimeError(result["error"])
                else:
                    result = run_prefix_recovery()
                    if "error" in result:
                        raise RuntimeError(result["error"])
                metrics = fetch_cache_metrics()
            finally:
                pf.terminate()
                pf.wait(timeout=10)

            data[name] = {
                "status": "OK",
                "config": {"gpu_util": gpu, "seqs": seqs, "cpu_gb": cpu},
                "kind": kind,
                "result": result,
                "metrics": metrics,
            }
            print(f"  ✅ {name} 完成: {json.dumps(result, ensure_ascii=False)[:220]}", flush=True)
            with open(RESULTS, "w") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            data[name] = {"status": "ERROR", "config": {"gpu_util": gpu, "seqs": seqs, "cpu_gb": cpu}, "error": str(e)}
            print(f"  ❌ {name}: {e}", flush=True)
    return data

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None, help="只跑某组合名 (验证用)")
    args = ap.parse_args()

    global MATRIX
    if args.only:
        MATRIX = [m for m in MATRIX if m[0] == args.only]

    data = {}
    if os.path.exists(RESULTS):
        with open(RESULTS) as f:
            data = json.load(f)

    print(f"共 {len(MATRIX)} 组合待跑. 结果将写入 {RESULTS}", flush=True)
    data = collect_matrix(data)

    # 恢复 BASE 配置
    print(f"\n{'='*60}\n恢复 BASE 配置 (gpu={BASE[0]} seqs={BASE[1]} cpu={BASE[2]}GB)", flush=True)
    try:
        apply_config(*BASE)
        print("✅ 已恢复 BASE 并重启", flush=True)
    except Exception as e:
        print(f"⚠️ 恢复失败: {e} — 需手动恢复", flush=True)

    print("\n=== 矩阵完成, 汇总 ===")
    for name in [m[0] for m in MATRIX]:
        d = data.get(name, {})
        if d.get("kind") == "load":
            r = d.get("result", {})
            print(f"  {name}: status={d.get('status')} qps={r.get('qps')} ttft_p95={r.get('ttft_p95')} tok_s={r.get('output_tokens_per_s')}")
        else:
            r = d.get("result", {})
            print(f"  {name}: status={d.get('status')} evicted_ttft_median={r.get('evicted_ttft_median')} hit_ratio={d.get('metrics',{}).get('hit_ratio')}")

if __name__ == "__main__":
    main()
