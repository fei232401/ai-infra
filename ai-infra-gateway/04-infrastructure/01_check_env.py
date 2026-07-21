"""
阶段1环境检测脚本：验证 Ollama 安装、GPU、模型是否就绪
用法：python scripts/01_check_env.py
"""
import subprocess
import json
import os

def run(cmd):
    """运行命令，返回 stdout"""
    try:
        return subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT).decode(errors="ignore").strip()
    except subprocess.CalledProcessError as e:
        return f"FAILED: {e.output.decode(errors='ignore').strip() if e.output else 'exit code ' + str(e.returncode)}"
    except FileNotFoundError:
        return "NOT FOUND"

def check_gpu():
    """GPU 显存检查"""
    print("\n=== GPU 显存检查 ===")
    out = run('nvidia-smi --query-gpu=name,memory.used,memory.free,memory.total --format=csv,noheader')
    print(out)
    # 解析显存值，校验是否在6.5-7.5 GB验收范围内
    if "FAILED" not in out and "NOT FOUND" not in out:
        parts = out.split(", ")
        if len(parts) >= 3:
            mem_used_mb = int(parts[1].replace(" MiB", ""))
            mem_used_gb = mem_used_mb / 1024
            print(f"  显存已用: {mem_used_gb:.2f} GB")
            if 6.5 <= mem_used_gb <= 7.5:
                print("  ✅ 显存占用达标 (6.5-7.5 GB)")
            else:
                print(f"  ⚠️ 显存占用未达标，当前 {mem_used_gb:.2f} GB，目标 6.5-7.5 GB")

def check_ollama():
    """Ollama 安装检查"""
    print("\n=== Ollama 安装检查 ===")
    version = run("ollama --version")
    print(f"  ollama: {version}")
    
    # 检查进程
    tasklist = run('tasklist /FI "IMAGENAME eq ollama.exe"')
    if "ollama.exe" in tasklist:
        print("  Ollama 进程: ✅ 运行中")
    else:
        print("  Ollama 进程: ❌ 未运行，需要启动")

def check_models():
    """已安装模型检查"""
    print("\n=== 模型拉取检查 ===")
    try:
        import urllib.request
        req = urllib.request.Request("http://localhost:11434/api/tags")
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read().decode())
        models = data.get("models", [])
        if models:
            print(f"  已安装 {len(models)} 个模型:")
            for m in models:
                name = m.get("name", "?")
                size = m.get("size", 0)
                size_gb = size / (1024**3)
                print(f"    - {name} ({size_gb:.2f} GB)")
        else:
            print("  ⚠️ 没有已安装的模型，需要拉取")
    except Exception as e:
        print(f"  ❌ 无法连接 Ollama: {e}")

if __name__ == "__main__":
    print("=" * 60)
    print("  AI Infra 阶段1 环境检测")
    print("=" * 60)
    check_gpu()
    check_ollama()
    check_models()
    print("\n✅ 检测完成")