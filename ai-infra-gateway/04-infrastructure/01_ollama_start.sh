#!/bin/bash
# ============================================================
# 阶段1：Ollama 推理引擎启动 & 显存榨取脚本
# 目标：在 Windows 4060 上跑 Ollama，多模型驻留，榨干8G显存
# 使用方式：Git Bash 中运行  bash scripts/01_ollama_start.sh
# ============================================================

set -euo pipefail

# ----- 颜色输出 -----
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
err() { echo -e "${RED}[ERROR]${NC} $1"; }

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║    AI Infra 阶段1：Ollama 推理引擎启动              ║"
echo "║    目标：多模型驻留 | KV Cache 预热 | 显存榨取       ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# ==================== 第一步：环境变量配置 ====================
# 核心概念（庖丁解牛）：
#   OLLAMA_FLASH_ATTENTION=1  → 开启Flash Attention，能省约20-30%显存
#     比喻：就像整理仓库，Flash Attention不再是"每次去货架拿东西都走一遍整个仓库"
#     而是把注意力集中在最相关的几个货架上，跳过无关区域，省下大量"脑内运算"
#
#   OLLAMA_NUM_PARALLEL=4     → 允许同时处理4个并发请求
#     比喻：让4060同时开4个收银通道，而不是1个通道排队
#
#   OLLAMA_KV_CACHE_TYPE=f16  → 使用FP16精度存储KV Cache（默认FP32），再省一半
#   OLLAMA_MAX_LOADED_MODELS=2 → 最多同时驻留2个模型在显存
#
#   CUDA_VISIBLE_DEVICES=0   → 明确指定只用第一块GPU（你只有一块4060，防意外）

export OLLAMA_HOST="0.0.0.0:11434"
export OLLAMA_NUM_PARALLEL=4
export OLLAMA_FLASH_ATTENTION=1
export OLLAMA_KV_CACHE_TYPE=f16
export OLLAMA_MAX_LOADED_MODELS=2
export CUDA_VISIBLE_DEVICES=0

info "环境变量已设置："
info "  OLLAMA_NUM_PARALLEL=4 (4路并发)"
info "  OLLAMA_FLASH_ATTENTION=1 (省显存)"
info "  OLLAMA_KV_CACHE_TYPE=f16 (半精度KV Cache)"
info "  OLLAMA_MAX_LOADED_MODELS=2 (双模型驻留)"
info "  OLLAMA_HOST=0.0.0.0:11434"

# ==================== 第二步：检查 Ollama 服务状态 ====================
echo ""
info "检查 Ollama 服务状态..."

# 1. 检查进程
if tasklist 2>/dev/null | grep -qi "ollama"; then
    info "Ollama 进程已在运行，先停止旧实例..."
    taskkill //F //IM ollama.exe 2>/dev/null || true
    sleep 2
fi

# 2. 检查二进制是否存在
OLLAMA_BIN=""
for candidate in \
    "/c/Users/admin/AppData/Local/Programs/Ollama/ollama.exe" \
    "/c/Program Files/Ollama/ollama.exe" \
    "ollama"; do
    if command -v "$candidate" &>/dev/null || [ -f "$candidate" ]; then
        OLLAMA_BIN="$candidate"
        break
    fi
done

if [ -z "$OLLAMA_BIN" ]; then
    err "找不到 ollama.exe！请先通过 winget 或官网安装 Ollama。"
    err "  winget install Ollama.Ollama"
    err "  或者: https://ollama.com/download/windows"
    exit 1
fi

info "Ollama 二进制: $OLLAMA_BIN"

# ==================== 第三步：启动 Ollama 服务 ====================
echo ""
info "启动 Ollama 推理服务..."
"$OLLAMA_BIN" serve > /tmp/ollama_serve.log 2>&1 &
OLLAMA_PID=$!
info "Ollama 进程 PID: $OLLAMA_PID"

# 等待服务就绪（最多等30秒）
echo -n "等待服务就绪"
for i in $(seq 1 30); do
    if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
        echo ""
        info "Ollama 服务就绪！"
        break
    fi
    echo -n "."
    sleep 1
    if [ $i -eq 30 ]; then
        err "Ollama 启动超时，查看日志: cat /tmp/ollama_serve.log"
        exit 1
    fi
done

# ==================== 第四步：拉取模型（如未缓存） ====================
# 用 qwen2.5:1.5b（主力）和 qwen2.5:0.5b（辅模型）
# 显存计算（庖丁解牛）：
#   - qwen2.5:1.5b (FP16) ≈ 1.5B × 2 bytes = 3GB 模型权重
#   - qwen2.5:0.5b (FP16) ≈ 0.5B × 2 bytes = 1GB 模型权重
#   - 两个模型 ≈ 4GB 权重
#   - KV Cache + 运行时 ≈ 2-3GB (Flash Attention 可压缩)
#   - 总占用 ≈ 6-7GB，刚好落在 8GB 4060 的最佳区间 (6.5-7.5GB)
echo ""
info "检查模型缓存..."

pull_if_missing() {
    local MODEL="$1"
    if curl -s http://localhost:11434/api/tags | grep -q "\"name\":\"$MODEL\""; then
        info "模型 $MODEL 已缓存 ✓"
    else
        info "拉取模型: $MODEL ..."
        "$OLLAMA_BIN" pull "$MODEL"
        info "模型 $MODEL 拉取完成 ✓"
    fi
}

pull_if_missing "qwen2.5:1.5b"
pull_if_missing "qwen2.5:0.5b"

# ==================== 第五步：预热双模型到显存 ====================
# 原理（庖丁解牛）：
#   Ollama 有"惰性加载"机制——不主动使用的话模型不会常驻显存
#   首次推理请求会触发模型加载（冷启动延迟 2-5s）
#   我们提前发一发热身请求，把两个模型都"揣进显存"里
echo ""
info "预热模型到显存（消除冷启动延迟）..."

warm_model() {
    local MODEL="$1"
    info "  预热 $MODEL ..."
    curl -s http://localhost:11434/api/generate \
        -d "{\"model\":\"$MODEL\",\"prompt\":\"Hello\",\"stream\":false}" \
        > /dev/null
    info "  $MODEL 已驻留显存 ✓"
}

warm_model "qwen2.5:1.5b"
warm_model "qwen2.5:0.5b"

# ==================== 第六步：显存报告 ====================
echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║              显存占用报告 (GPU 0)                    ║"
echo "╚══════════════════════════════════════════════════════╝"

nvidia-smi --id=0 --query-gpu=name,memory.used,memory.free,memory.total,temperature.gpu,utilization.gpu --format=csv,noheader

echo ""
info "阶段1 环境初始化完毕！"
info "Ollama 推理服务运行中: http://localhost:11434"
info ""
info "验证命令（在另一个 Git Bash 窗口运行）："
info "  curl http://localhost:11434/api/tags          # 查看已加载模型"
info "  curl http://localhost:11434/api/generate -d '{\"model\":\"qwen2.5:1.5b\",\"prompt\":\"你好\"}'  # 快速测试"