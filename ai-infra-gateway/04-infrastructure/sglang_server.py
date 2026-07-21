#!/usr/bin/env python3
"""
sglang 推理服务器启动脚本
Windows 原生运行，无需 Docker/WSL2
使用 sglang 的 OpenAI 兼容 API 暴露端点
"""
import subprocess
import sys
import os

# sglang 命令行启动
# --model-path: 模型路径（支持 HuggingFace ID 或本地路径）
# --host: 监听地址
# --port: 监听端口（默认 30000）
# --mem-fraction-static: GPU 显存分配给 KV Cache 的比例（默认 0.88）
# --context-length: 上下文长度

CMD = [
    sys.executable, "-m", "sglang.launch_server",
    "--model-path", "Qwen/Qwen2.5-1.5B-Instruct",
    "--host", "0.0.0.0",
    "--port", "30000",
    "--mem-fraction-static", "0.85",
    "--context-length", "2048",
    "--trust-remote-code",
]

print("=" * 60)
print("  Starting sglang Inference Server")
print("  Model: Qwen2.5-1.5B-Instruct")
print("  Endpoint: http://localhost:30000")
print("  OpenAI-compatible API: http://localhost:30000/v1")
print("=" * 60)

if __name__ == "__main__":
    os.environ["SGLANG_USE_FLASHINFER"] = "1"  # 启用 FlashInfer 加速
    subprocess.run(CMD)