#!/bin/bash
# =============================================================================
# vLLM Deploy Script — AutoDL RTX 4090 Instance
# Usage: bash vllm_deploy.sh
# Expected runtime: ~15 minutes (downloads dominate)
# =============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

# ---------- Step 0: Environment Check ----------
log_info "Step 0: Checking environment..."

# Verify GPU
if ! command -v nvidia-smi &>/dev/null; then
    log_error "nvidia-smi not found — is this an NVIDIA GPU instance?"
    exit 1
fi

GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
GPU_MEM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader 2>/dev/null | head -1)
log_info "Detected GPU: ${GPU_NAME} (${GPU_MEM})"

# Verify CUDA
CUDA_VER=$(nvidia-smi | grep -oP 'CUDA Version: \K[0-9.]+' || echo "unknown")
if [[ "${CUDA_VER}" < "12.1" ]]; then
    log_error "CUDA ${CUDA_VER} detected. vLLM requires CUDA >= 12.1"
    exit 1
fi
log_info "CUDA Version: ${CUDA_VER}"

# Check Python
PYTHON_VER=$(python3 --version 2>&1 | awk '{print $2}')
log_info "Python: ${PYTHON_VER}"

# Check disk space (need ~20GB for models)
AVAIL_GB=$(df -BG . | tail -1 | awk '{print $4}' | sed 's/G//')
if (( AVAIL_GB < 25 )); then
    log_error "Only ${AVAIL_GB}GB free disk. Need >= 25GB for models."
    exit 1
fi
log_info "Available disk: ${AVAIL_GB}GB"

# ---------- Step 1: Install vLLM ----------
log_info "Step 1: Installing vLLM..."

pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple 2>/dev/null || true
pip install vllm openai aiohttp -q 2>&1 | tail -3

# Verify installation
python3 -c "import vllm; print(f'vLLM {vllm.__version__} installed')"

# ---------- Step 2: Download Models (ModelScope, GFW-safe) ----------
log_info "Step 2: Downloading models from ModelScope (qwen2.5 0.5B + 1.5B)..."

pip install modelscope -q 2>&1 | tail -1

mkdir -p ~/models

# qwen2.5:1.5B (986 MB)
if [ -d ~/models/Qwen2.5-1.5B-Instruct ]; then
    log_info "qwen2.5-1.5b already downloaded, skipping."
else
    log_info "Downloading qwen2.5-1.5b (986 MB)..."
    python3 -c "
from modelscope import snapshot_download
snapshot_download('Qwen/Qwen2.5-1.5B-Instruct', cache_dir='$HOME/models')
"
    log_info "qwen2.5-1.5b download complete."
fi

# qwen2.5:0.5B (397 MB)
if [ -d ~/models/Qwen2.5-0.5B-Instruct ]; then
    log_info "qwen2.5-0.5b already downloaded, skipping."
else
    log_info "Downloading qwen2.5-0.5b (397 MB)..."
    python3 -c "
from modelscope import snapshot_download
snapshot_download('Qwen/Qwen2.5-0.5B-Instruct', cache_dir='$HOME/models')
"
    log_info "qwen2.5-0.5b download complete."
fi

# ---------- Step 3: Start vLLM Server (1.5B first, as warm-up) ----------
log_info "Step 3: Starting vLLM OpenAI-compatible server with qwen2.5-1.5b..."

# Find model path
MODEL_1_5B=$(find ~/models -name "Qwen2.5-1.5B-Instruct" -type d 2>/dev/null | head -1)
if [ -z "${MODEL_1_5B}" ]; then
    log_error "Model path not found for Qwen2.5-1.5B-Instruct"
    log_error "Searched under: ~/models/"
    find ~/models -maxdepth 3 -type d 2>/dev/null || log_error "~/models/ directory structure:"
    exit 1
fi

log_info "Model path: ${MODEL_1_5B}"

# Kill any existing vLLM process
pkill -f "vllm.entrypoints.openai" 2>/dev/null || true
sleep 2

# Start server
nohup python3 -m vllm.entrypoints.openai.api_server \
    --model "${MODEL_1_5B}" \
    --trust-remote-code \
    --gpu-memory-utilization 0.90 \
    --max-model-len 4096 \
    --port 8000 \
    > /tmp/vllm_server.log 2>&1 &

VLLM_PID=$!
log_info "vLLM server PID: ${VLLM_PID}"

# ---------- Step 4: Wait for Server Ready ----------
log_info "Step 4: Waiting for vLLM server to be ready (may take 30-90s)..."

MAX_WAIT=180
ELAPSED=0
READY=false

while [ $ELAPSED -lt $MAX_WAIT ]; do
    if curl -s http://localhost:8000/health > /dev/null 2>&1; then
        READY=true
        break
    fi
    sleep 5
    ELAPSED=$((ELAPSED + 5))
    echo -n "."
done
echo ""

if [ "$READY" = true ]; then
    log_info "vLLM server ready after ${ELAPSED}s!"
else
    log_error "vLLM failed to start within ${MAX_WAIT}s."
    log_error "Last 20 lines of server log:"
    tail -20 /tmp/vllm_server.log
    exit 1
fi

# ---------- Step 5: Warm-up Inference ----------
log_info "Step 5: Sending warm-up inference request..."

python3 -c "
import requests, json, time
url = 'http://localhost:8000/v1/chat/completions'
payload = {
    'model': '${MODEL_1_5B}',
    'messages': [{'role': 'user', 'content': 'Say hello in one word.'}],
    'max_tokens': 10
}
t0 = time.time()
r = requests.post(url, json=payload)
elapsed = time.time() - t0
if r.status_code == 200:
    print(f'Warm-up OK ({elapsed:.1f}s): {r.json()[\"choices\"][0][\"message\"][\"content\"].strip()}')
else:
    print(f'Warm-up FAILED (HTTP {r.status_code}): {r.text[:200]}')
    exit(1)
"

# ---------- Step 6: Summary ----------
echo ""
log_info "============================================"
log_info "  vLLM Deploy Complete!"
log_info "  Server:    http://localhost:8000"
log_info "  API docs:  http://localhost:8000/docs"
log_info "  PID:       ${VLLM_PID}"
log_info "  Log:       /tmp/vllm_server.log"
log_info ""
log_info "  Models ready:"
log_info "    qwen2.5-1.5B  → ${MODEL_1_5B}"
log_info "    qwen2.5-0.5B  → $(find ~/models -name 'Qwen2.5-0.5B-Instruct' -type d 2>/dev/null | head -1)"
log_info ""
log_info "  Next: python3 vllm_benchmark.py"
log_info "============================================"