"""
ModelScope GGUF 下载 + Ollama 本地导入
绕过 registry.ollama.ai 被墙问题
"""
import os
import sys
import urllib.request
import subprocess
import json

OLLAMA_BIN = r"C:\Users\admin\AppData\Local\Programs\Ollama\ollama.exe"
MODELS_DIR = os.path.expanduser(r"~\.ollama\models")

# ModelScope GGUF 文件地址
MODELS = {
    "qwen2.5:0.5b": {
        "gguf_url": "https://modelscope.cn/models/qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/master/qwen2.5-0.5b-instruct-q4_k_m.gguf",
        "modelfile": """FROM ./qwen2.5-0.5b-instruct-q4_k_m.gguf
TEMPLATE """{{ .Prompt }}"""
PARAMETER temperature 0.7
PARAMETER top_p 0.8
PARAMETER num_ctx 2048
""",
    },
    "qwen2.5:1.5b": {
        "gguf_url": "https://modelscope.cn/models/qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/master/qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "modelfile": """FROM ./qwen2.5-1.5b-instruct-q4_k_m.gguf
TEMPLATE """{{ .Prompt }}"""
PARAMETER temperature 0.7
PARAMETER top_p 0.8
PARAMETER num_ctx 2048
""",
    },
}

def download(url, dest, desc):
    """带进度条的下载"""
    print(f"\n[下载] {desc}")
    print(f"  URL: {url}")
    print(f"  保存到: {dest}")

    if os.path.exists(dest):
        size_mb = os.path.getsize(dest) / (1024 * 1024)
        print(f"  文件已存在 ({size_mb:.1f} MB)，跳过下载")
        return True

    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        })
        with urllib.request.urlopen(req, timeout=600) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 1024 * 1024
            with open(dest, "wb") as f:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        pct = downloaded / total * 100
                        mb = downloaded / (1024 * 1024)
                        total_mb = total / (1024 * 1024)
                        print(f"  {pct:.0f}% ({mb:.1f}/{total_mb:.1f} MB)", end="\r")
            print(f"\n  下载完成: {downloaded / (1024*1024):.1f} MB")
            return True
    except Exception as e:
        print(f"\n  下载失败: {e}")
        return False

def create_ollama_model(model_name, gguf_path, modelfile_content):
    """用 Modelfile 创建 Ollama 模型"""
    print(f"\n[导入] {model_name}")

    modelfile_path = os.path.join(os.path.dirname(gguf_path), f"Modelfile.{model_name.replace(':','_')}")
    with open(modelfile_path, "w", encoding="utf-8") as f:
        f.write(modelfile_content)

    print(f"  Modelfile: {modelfile_path}")
    print(f"  GGUF: {gguf_path}")

    cmd = [OLLAMA_BIN, "create", model_name, "-f", modelfile_path]
    print(f"  执行: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    print(f"  stdout: {result.stdout.strip()}")
    if result.returncode != 0:
        print(f"  stderr: {result.stderr.strip()}")
        return False
    return True

def main():
    print("=" * 60)
    print("  ModelScope GGUF -> Ollama 模型导入")
    print("=" * 60)

    os.makedirs(MODELS_DIR, exist_ok=True)

    for model_name, cfg in MODELS.items():
        gguf_filename = os.path.basename(cfg["gguf_url"])
        gguf_path = os.path.join(MODELS_DIR, gguf_filename)

        if download(cfg["gguf_url"], gguf_path, model_name):
            if create_ollama_model(model_name, gguf_path, cfg["modelfile"]):
                print(f"  [OK] {model_name} 导入成功")
            else:
                print(f"  [FAIL] {model_name} 导入失败")

    # 验证
    print("\n[验证] 已安装模型:")
    subprocess.run([OLLAMA_BIN, "list"], check=False)

    print("\nDone.")

if __name__ == "__main__":
    main()