#!/usr/bin/env python3
"""
AI Infra 本地可视化仪表盘
浏览器打开 http://localhost:9090 即可看到实时 GPU 指标图表
Zero 外部依赖 — 纯 Python + FastAPI + matplotlib
"""
import time
import json
import io
import base64
import os
import urllib.request
from collections import deque
from datetime import datetime

import pynvml
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import uvicorn

# ============ 数据缓冲（最近 5 分钟 = 100 个点每秒3秒 = 100点）============
MAX_POINTS = 100
history = {
    "timestamps": deque(maxlen=MAX_POINTS),
    "gpu_mem_used": deque(maxlen=MAX_POINTS),
    "gpu_mem_free": deque(maxlen=MAX_POINTS),
    "gpu_util": deque(maxlen=MAX_POINTS),
    "gpu_temp": deque(maxlen=MAX_POINTS),
    "ollama_models": deque(maxlen=MAX_POINTS),
    "ollama_up": deque(maxlen=MAX_POINTS),
}

app = FastAPI(title="AI Infra Dashboard")

# ============ GPU 采集 ============
def collect_gpu():
    """采集 GPU 实时指标，返回字典"""
    try:
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
        util = pynvml.nvmlDeviceGetUtilizationRates(handle)
        temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
        pynvml.nvmlShutdown()
        return {
            "gpu_mem_used": mem.used // (1024 * 1024),
            "gpu_mem_free": mem.free // (1024 * 1024),
            "gpu_mem_total": mem.total // (1024 * 1024),
            "gpu_util": util.gpu,
            "gpu_temp": temp,
        }
    except Exception as e:
        return {"error": str(e)}

def collect_ollama():
    """采集 Ollama 服务状态"""
    try:
        resp = urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3)
        data = json.loads(resp.read().decode())
        models = [m["name"] for m in data.get("models", [])]
        return {"up": True, "models": models, "count": len(models)}
    except:
        return {"up": False, "models": [], "count": 0}

# ============ 图表生成 ============
def generate_chart():
    """生成 4 合 1 GPU 实时趋势图，返回 base64 PNG"""
    if len(history["timestamps"]) < 2:
        return ""

    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    fig.suptitle("RTX 4060 Laptop GPU — 实时监控", fontsize=14, fontweight="bold")
    t = list(history["timestamps"])

    # 图1：显存使用
    ax = axes[0, 0]
    ax.fill_between(t, list(history["gpu_mem_used"]), alpha=0.3, color="steelblue")
    ax.plot(t, list(history["gpu_mem_used"]), color="steelblue", linewidth=2)
    ax.axhline(y=8188, color="gray", linestyle="--", alpha=0.5, label="Total 8GB")
    ax.set_ylabel("MiB")
    ax.set_title("显存用量")
    ax.legend(loc="upper right", fontsize=8)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))

    # 图2：空闲显存
    ax = axes[0, 1]
    ax.fill_between(t, list(history["gpu_mem_free"]), alpha=0.3, color="seagreen")
    ax.plot(t, list(history["gpu_mem_free"]), color="seagreen", linewidth=2)
    ax.set_ylabel("MiB")
    ax.set_title("空闲显存")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))

    # 图3：GPU 利用率
    ax = axes[1, 0]
    ax.plot(t, list(history["gpu_util"]), color="darkorange", linewidth=2)
    ax.fill_between(t, list(history["gpu_util"]), alpha=0.2, color="darkorange")
    ax.set_ylabel("%")
    ax.set_ylim(0, 100)
    ax.set_title("GPU 利用率")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))

    # 图4：温度
    ax = axes[1, 1]
    ax.plot(t, list(history["gpu_temp"]), color="crimson", linewidth=2)
    ax.fill_between(t, list(history["gpu_temp"]), alpha=0.2, color="crimson")
    ax.set_ylabel("°C")
    ax.set_title("GPU 温度")
    ax.axhline(y=80, color="red", linestyle="--", alpha=0.5, label="Throttle 80°C")
    ax.legend(loc="upper right", fontsize=8)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))

    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=80, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode()

# ============ API 路由 ============
@app.get("/api/now")
async def api_now():
    """返回当前时刻的即时快照"""
    gpu = collect_gpu()
    ollama = collect_ollama()
    return {"gpu": gpu, "ollama": ollama, "history_points": len(history["timestamps"])}

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """主仪表盘页面 — 3秒自动刷新"""
    gpu = collect_gpu()
    ollama = collect_ollama()
    chart_b64 = generate_chart()

    # 更新历史缓存
    now = datetime.now()
    history["timestamps"].append(now)
    history["gpu_mem_used"].append(gpu.get("gpu_mem_used", 0))
    history["gpu_mem_free"].append(gpu.get("gpu_mem_free", 0))
    history["gpu_util"].append(gpu.get("gpu_util", 0))
    history["gpu_temp"].append(gpu.get("gpu_temp", 0))
    history["ollama_up"].append(1 if ollama["up"] else 0)
    history["ollama_models"].append(ollama["count"])

    # 计算显存占比
    mem_used = gpu.get("gpu_mem_used", 0)
    mem_total = gpu.get("gpu_mem_total", 8188)
    mem_pct = mem_used / mem_total * 100 if mem_total else 0
    bar_color = "#4CAF50" if mem_pct < 50 else "#FF9800" if mem_pct < 85 else "#f44336"

    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta http-equiv="refresh" content="3">
<title>AI Infra 实时仪表盘</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:#0d1117; color:#c9d1d9; font-family:'Segoe UI',system-ui,sans-serif; padding:20px; }}
  h1 {{ font-size:1.5em; margin-bottom:5px; color:#58a6ff; }}
  .subtitle {{ color:#8b949e; font-size:0.85em; margin-bottom:20px; }}
  .grid {{ display:grid; grid-template-columns:repeat(4,1fr); gap:15px; margin-bottom:20px; }}
  .card {{ background:#161b22; border:1px solid #30363d; border-radius:8px; padding:16px; text-align:center; }}
  .card .label {{ font-size:0.8em; color:#8b949e; text-transform:uppercase; margin-bottom:8px; }}
  .card .value {{ font-size:2em; font-weight:bold; }}
  .mem-bar {{ width:100%; height:24px; background:#21262d; border-radius:12px; overflow:hidden; margin-top:8px; }}
  .mem-fill {{ height:100%; background:{bar_color}; border-radius:12px; transition:width 0.5s; }}
  .chart-container {{ background:#161b22; border:1px solid #30363d; border-radius:8px; padding:10px; margin-bottom:20px; }}
  .chart-container img {{ width:100%; }}
  .footer {{ text-align:center; color:#484f58; font-size:0.75em; margin-top:20px; }}
  .status-dot {{ display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:6px; }}
  .status-up {{ background:#3fb950; box-shadow:0 0 6px #3fb950; }}
  .status-down {{ background:#f85149; box-shadow:0 0 6px #f85149; }}
</style>
</head>
<body>

<h1>🖥️ AI Infra 推理网关 — 实时仪表盘</h1>
<p class="subtitle">RTX 4060 Laptop GPU (8GB) | 每3秒自动刷新 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>

<!-- 指标卡片 -->
<div class="grid">
  <div class="card">
    <div class="label">显存已用</div>
    <div class="value" style="color:{bar_color}">{mem_used:,} MiB</div>
    <div class="mem-bar"><div class="mem-fill" style="width:{mem_pct:.1f}%"></div></div>
    <div style="font-size:0.75em;color:#8b949e;margin-top:6px">{mem_pct:.1f}% / 8,188 MiB</div>
  </div>
  <div class="card">
    <div class="label">显存空闲</div>
    <div class="value" style="color:#3fb950">{gpu.get('gpu_mem_free',0):,} MiB</div>
    <div style="font-size:0.85em;color:#8b949e;margin-top:10px">可用容量</div>
  </div>
  <div class="card">
    <div class="label">GPU 利用率</div>
    <div class="value" style="color:#d2991d">{gpu.get('gpu_util',0)}%</div>
    <div style="font-size:0.85em;color:#8b949e;margin-top:10px">CUDA Core</div>
  </div>
  <div class="card">
    <div class="label">GPU 温度</div>
    <div class="value" style="color:{'#f85149' if gpu.get('gpu_temp',0)>75 else '#d2991d' if gpu.get('gpu_temp',0)>60 else '#3fb950'}">{gpu.get('gpu_temp','?')}°C</div>
    <div style="font-size:0.85em;color:#8b949e;margin-top:10px">安全范围 < 80°C</div>
  </div>
</div>

<!-- Ollama 状态 -->
<div style="display:flex; gap:15px; margin-bottom:20px;">
  <div class="card" style="flex:1">
    <div class="label">Ollama 服务</div>
    <div style="font-size:1.2em; margin-top:5px">
      <span class="status-dot {'status-up' if ollama['up'] else 'status-down'}"></span>
      {'<b style=color:#3fb950>运行中</b>' if ollama['up'] else '<b style=color:#f85149>不可达</b>'}
    </div>
  </div>
  <div class="card" style="flex:1">
    <div class="label">已加载模型</div>
    <div class="value" style="font-size:1.5em">{ollama['count']}</div>
    <div style="font-size:0.75em;color:#8b949e;margin-top:4px">{', '.join(ollama['models']) if ollama['models'] else '无'}</div>
  </div>
  <div class="card" style="flex:1">
    <div class="label">网关状态</div>
    <div style="font-size:1.2em; margin-top:5px">
      <span class="status-dot status-up"></span>
      <b style="color:#3fb950">运行中</b>
    </div>
    <div style="font-size:0.75em;color:#8b949e;margin-top:4px">http://localhost:8000</div>
  </div>
</div>

<!-- 趋势图 -->
<div class="chart-container">
  <img src="data:image/png;base64,{chart_b64}" alt="GPU Trend">
</div>

<div class="footer">
  AI Infra Gateway v1.0 | Dashboard | {history['timestamps'][-1].strftime('%H:%M:%S') if history['timestamps'] else '...'} | 数据点数: {len(history['timestamps'])}/100
</div>

</body>
</html>"""

if __name__ == "__main__":
    import threading
    # 后台线程持续采集数据（供图表使用）
    def bg_collector():
        while True:
            gpu = collect_gpu()
            ollama = collect_ollama()
            now = datetime.now()
            history["timestamps"].append(now)
            history["gpu_mem_used"].append(gpu.get("gpu_mem_used", 0))
            history["gpu_mem_free"].append(gpu.get("gpu_mem_free", 0))
            history["gpu_util"].append(gpu.get("gpu_util", 0))
            history["gpu_temp"].append(gpu.get("gpu_temp", 0))
            history["ollama_up"].append(1 if ollama["up"] else 0)
            history["ollama_models"].append(ollama["count"])
            time.sleep(3)

    t = threading.Thread(target=bg_collector, daemon=True)
    t.start()

    print("=" * 50)
    print("  AI Infra Dashboard 启动")
    print("  浏览器打开: http://localhost:9090")
    print("  实时 GPU 图表 + Ollama 状态")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=9090, log_level="warning")