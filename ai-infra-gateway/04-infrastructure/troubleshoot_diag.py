"""T-001 + T-002 联合诊断脚本"""
import socket
import os
import sys

results = []

# === T-001: Registry 连通性 ===
results.append("=== T-001: Ollama Registry 连通性 ===")
for host, port in [("registry.ollama.ai", 443), ("models.ollama.ai", 443)]:
    try:
        s = socket.socket()
        s.settimeout(8)
        r = s.connect_ex((host, port))
        s.close()
        results.append(f"  {host}:{port} -> {'OK' if r == 0 else 'FAIL (errno=' + str(r) + ')'}")
    except Exception as e:
        results.append(f"  {host}:{port} -> EXCEPTION: {e}")

# === T-002: Python 导入 ===
results.append("\n=== T-002: Python 关键导入 ===")
for mod in ["fastapi", "uvicorn", "aiohttp", "yaml", "pynvml", "pydantic"]:
    try:
        __import__(mod)
        results.append(f"  {mod}: OK")
    except Exception as e:
        results.append(f"  {mod}: FAIL - {e}")

# === 配置加载测试 ===
results.append("\n=== T-002b: 配置加载测试 ===")
try:
    import yaml
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "gateway_config.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    results.append(f"  配置文件: {config_path}")
    results.append(f"  解析成功: server.port={cfg['server']['port']}, ollama={cfg['ollama']['base_url']}")
except Exception as e:
    results.append(f"  配置加载 FAIL: {e}")

# === 写入文件 ===
output = "\n".join(results)
print(output)
with open(r"C:\Users\admin\Desktop\troubleshoot_result.txt", "w", encoding="utf-8") as f:
    f.write(output)
print("\n[DONE] 结果已写入 troubleshoot_result.txt")