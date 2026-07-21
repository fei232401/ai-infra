#!/bin/bash
# =============================================================================
# AI Infra Gateway — AutoDL One-Click Setup & Benchmark
# =============================================================================
# PASTE THIS ENTIRE BLOCK into your SSH session:
#
#   ssh -p 47616 root@connect.cqa1.seetacloud.com
#   (enter password: IZLrfVZy9Gdj)
#
# Then run:
#   bash <(curl -s https://raw.githubusercontent.com/...)
#   OR: paste the commands below line by line
# =============================================================================

set -euo pipefail

GREEN='\033[0;32m'
NC='\033[0m'
log() { echo -e "${GREEN}[+]${NC} $*"; }

log "=== AI Infra Gateway — AutoDL Autopilot ==="
log "Estimated total time: ~45 minutes"

# ---- STEP 0: Environment check ----
log "Step 0: Checking environment..."
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
python3 --version
echo "CUDA: $(nvidia-smi | grep -oP 'CUDA Version: \K[0-9.]+')"
echo "Disk: $(df -h / | tail -1 | awk '{print $4}') free"

# ---- STEP 1: Create workspace ----
log "Step 1: Creating workspace..."
mkdir -p /root/workspace/ai-infra-gateway/05-autodl-benchmark/data
mkdir -p /root/workspace/sre-lab
cd /root/workspace/ai-infra-gateway/05-autodl-benchmark

# ---- STEP 2: Download scripts from GitHub (or we'll create them inline) ----
log "Step 2: Preparing scripts..."

# If git clone fails, create scripts inline
if command -v git &>/dev/null; then
    cd /root/workspace
    git clone https://github.com/fei232401/sre-lab.git 2>/dev/null || log "sre-lab clone failed, continuing..."
    cd /root/workspace/ai-infra-gateway/05-autodl-benchmark
fi

# ---- STEP 3: Install vLLM ----
log "Step 3: Installing vLLM..."
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple 2>/dev/null || true
pip install vllm openai aiohttp modelscope -q 2>&1 | tail -3
python3 -c "import vllm; print(f'vLLM {vllm.__version__} OK')"

# ---- STEP 4: Download models ----
log "Step 4: Downloading qwen2.5 models from ModelScope..."
mkdir -p ~/models

python3 -c "
from modelscope import snapshot_download
import os

models = [
    ('Qwen/Qwen2.5-1.5B-Instruct', 'qwen2.5-1.5b'),
    ('Qwen/Qwen2.5-0.5B-Instruct', 'qwen2.5-0.5b'),
]

for model_id, name in models:
    target = os.path.expanduser(f'~/models/{name}')
    if os.path.isdir(target):
        print(f'{name}: already downloaded, skipping')
        continue
    print(f'Downloading {name}...')
    snapshot_download(model_id, cache_dir=os.path.expanduser('~/models'))
    print(f'{name}: done')
"

# Find model paths
MODEL_1_5B=$(find ~/models -name "Qwen2.5-1.5B-Instruct" -type d 2>/dev/null | head -1)
MODEL_0_5B=$(find ~/models -name "Qwen2.5-0.5B-Instruct" -type d 2>/dev/null | head -1)
echo "1.5B model: ${MODEL_1_5B}"
echo "0.5B model: ${MODEL_0_5B}"

# ---- STEP 5: Start vLLM ----
log "Step 5: Starting vLLM with qwen2.5-1.5b..."
pkill -f "vllm.entrypoints.openai" 2>/dev/null || true
sleep 2

nohup python3 -m vllm.entrypoints.openai.api_server \
    --model "${MODEL_1_5B}" \
    --trust-remote-code \
    --gpu-memory-utilization 0.90 \
    --max-model-len 4096 \
    --port 8000 \
    > /tmp/vllm_server.log 2>&1 &

log "Waiting for vLLM to be ready (up to 180s)..."
for i in $(seq 1 36); do
    if curl -s http://localhost:8000/health > /dev/null 2>&1; then
        log "vLLM ready! ($(($i * 5))s)"
        break
    fi
    sleep 5
    echo -n "."
done
echo ""

# Warm-up
log "Warming up..."
python3 -c "
import requests, time
t0 = time.time()
r = requests.post('http://localhost:8000/v1/chat/completions', json={
    'model': '${MODEL_1_5B}',
    'messages': [{'role':'user','content':'Hi'}],
    'max_tokens': 5
})
print(f'Warm-up: {time.time()-t0:.1f}s, HTTP {r.status_code}')
"

# ---- STEP 6: Run benchmark v3.0 ----
log "Step 6: Running vLLM benchmark (SRE-LAB aligned)..."

# Create a minimal benchmark script inline
cat > /tmp/run_benchmark.py << 'PYEOF'
import asyncio, aiohttp, json, time, statistics, os, sys

URL = "http://localhost:8000/v1/chat/completions"

MODELS = [
    ("qwen2.5-1.5b", "${MODEL_1_5B}"),
    ("qwen2.5-0.5b", "${MODEL_0_5B}"),
]

SCENARIOS = [
    {"name":"light","prompt":"Explain what a GPU is in one sentence.","max_tokens":50,"weight":50},
    {"name":"medium","prompt":"Explain GPU memory hierarchy including registers, shared memory, L1/L2 cache, and HBM in detail.","max_tokens":200,"weight":30},
    {"name":"heavy","prompt":"Explain GPU memory hierarchy, KV cache, PagedAttention, FlashAttention, and continuous batching in depth.","max_tokens":500,"weight":15},
    {"name":"health","prompt":"Say hello.","max_tokens":5,"weight":5},
]

STEPS = [1, 4, 8, 16, 32]
REQ_PER_STEP = 32
STEP_TIME = 60

async def send(session, model, scenario, conc, sid, sem):
    async with sem:
        t0 = time.monotonic()
        ttft = None; tokens = []; prev = t0; tok_cnt = 0
        try:
            async with session.post(URL, json={
                "model": model, "stream": True,
                "messages": [{"role":"user","content":scenario["prompt"]}],
                "max_tokens": scenario["max_tokens"]
            }, timeout=aiohttp.ClientTimeout(total=300)) as r:
                if r.status != 200:
                    body = await r.text()
                    return {"ok":False,"err":f"HTTP {r.status}: {body[:100]}",
                            "scenario":scenario["name"],"conc":conc}
                async for line in r.content:
                    line = line.decode().strip()
                    if not line or line.startswith(":") or not line.startswith("data: "):
                        continue
                    d = line[6:]
                    if d == "[DONE]": break
                    try:
                        chunk = json.loads(d)
                        c = chunk.get("choices",[{}])[0].get("delta",{}).get("content","")
                        if c:
                            now = time.monotonic()
                            if ttft is None: ttft = (now-t0)*1000
                            else: tokens.append((now-prev)*1000)
                            prev = now; tok_cnt += 1
                    except: pass
            total = (time.monotonic()-t0)*1000
            tpot = sum(tokens)/len(tokens) if tokens else 0
            return {"ok":True,"ttft":ttft or total,"tpot":tpot,"total":total,
                    "tokens":tok_cnt,"token_lats":tokens,"scenario":scenario["name"],"conc":conc}
        except Exception as e:
            return {"ok":False,"err":str(e)[:100],"scenario":scenario["name"],"conc":conc}

def aggregate(results):
    ok = [r for r in results if r["ok"]]
    all_toks = [x for r in ok for x in r["token_lats"]]
    total_tok = sum(r["tokens"] for r in ok)
    total_t = max(r["total"] for r in ok)/1000 if ok else 1
    ttfts = sorted(r["ttft"] for r in ok) if ok else [0]
    toks_s = sorted(all_toks) if all_toks else [0]
    return {
        "ok": f"{len(ok)}/{len(results)}",
        "ttft_avg": sum(ttfts)/len(ttfts),
        "ttft_p95": ttfts[min(int(len(ttfts)*0.95), len(ttfts)-1)],
        "tpot_avg": sum(r["tpot"] for r in ok)/len(ok) if ok else 0,
        "tps": total_tok/total_t if total_t > 0 else 0,
        "rps": len(ok)/total_t if total_t > 0 else 0,
        "tok_p99": toks_s[min(int(len(toks_s)*0.99), len(toks_s)-1)],
    }

async def benchmark_model(model_path, model_name):
    print(f"\n{'='*50}\n  {model_name}\n{'='*50}")
    all_data = {}
    async with aiohttp.ClientSession() as s:
        for sc in SCENARIOS:
            print(f"\n  Scenario: {sc['name']} (weight={sc['weight']}%)")
            sc_data = []
            for conc in STEPS:
                n = max(int(REQ_PER_STEP * sc["weight"] / 100), 4)
                sem = asyncio.Semaphore(conc)
                t0 = time.monotonic()
                tasks = [send(s, model_path, sc, conc, i, sem) for i in range(n)]
                results = await asyncio.gather(*tasks)
                t1 = time.monotonic()
                agg = aggregate(list(results))
                agg["conc"] = conc
                sc_data.append(agg)
                print(f"    C{conc:<4} {agg['ok']}  TTFT:{agg['ttft_avg']:.0f}ms  TPOT:{agg['tpot_avg']:.0f}ms  TPS:{agg['tps']:.0f}  RPS:{agg['rps']:.1f}")
                await asyncio.sleep(max(0, STEP_TIME - (t1-t0)))
            all_data[sc["name"]] = sc_data
    return all_data

import subprocess
data = {}
for name, path in MODELS:
    if not path or not os.path.isdir(path):
        print(f"SKIP {name}: model not found at {path}")
        continue
    print(f"\nSwitching vLLM to {name}...")
    subprocess.run(["pkill","-f","vllm.entrypoints.openai"], capture_output=True)
    import time; time.sleep(3)
    subprocess.Popen(["nohup","python3","-m","vllm.entrypoints.openai.api_server",
        "--model",path,"--trust-remote-code","--gpu-memory-utilization","0.90",
        "--max-model-len","4096","--port","8000"],
        stdout=open("/tmp/vllm.log","a"), stderr=subprocess.STDOUT)
    for i in range(36):
        try:
            import urllib.request
            if urllib.request.urlopen("http://localhost:8000/health",timeout=2).status==200:
                print("Ready!"); time.sleep(5); break
        except: pass
        time.sleep(5); print(".", end="", flush=True)
    data[name] = asyncio.run(benchmark_model(path, name))

os.makedirs("data", exist_ok=True)
with open("data/raw_benchmark.json","w") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
with open("data/benchmark_summary.json","w") as f:
    json.dump({"gpu": "AutoDL","models":data,"timestamp":time.ctime()}, f, indent=2)
print("\nBenchmark complete! data/saved")
PYEOF

# Now run it
cd /root/workspace/ai-infra-gateway/05-autodl-benchmark
python3 /tmp/run_benchmark.py

# ---- STEP 7: Generate summary ----
log "Step 7: Summary"
python3 -c "
import json
with open('data/benchmark_summary.json') as f:
    d = json.load(f)
print(json.dumps(d, indent=2, ensure_ascii=False)[:3000])
"

log "=== ALL DONE ==="
log "Results in: /root/workspace/ai-infra-gateway/05-autodl-benchmark/data/"
log "Download with: scp -P 47616 root@connect.cqa1.seetacloud.com:/root/workspace/ai-infra-gateway/05-autodl-benchmark/data/* ./"