#!/usr/bin/env python3
"""
AI Infra 推理网关 — 专业级实时仪表盘 v2
特性：暗色主题 + 4 面板 + 实时折线图 + 自动刷新
Zero 外部依赖，纯 Python + FastAPI + matplotlib
"""
import time, json, io, base64, os, urllib.request, threading
from collections import deque
from datetime import datetime
import pynvml, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import uvicorn

MAX_POINTS = 120
history = {
    "t": deque(maxlen=MAX_POINTS),
    "mem_used": deque(maxlen=MAX_POINTS),
    "mem_free": deque(maxlen=MAX_POINTS),
    "gpu_util": deque(maxlen=MAX_POINTS),
    "gpu_temp": deque(maxlen=MAX_POINTS),
    "models": deque(maxlen=MAX_POINTS),
}

app = FastAPI(title="AI Infra Dashboard v2")

def gpu_snapshot():
    try:
        pynvml.nvmlInit()
        h = pynvml.nvmlDeviceGetHandleByIndex(0)
        m = pynvml.nvmlDeviceGetMemoryInfo(h)
        u = pynvml.nvmlDeviceGetUtilizationRates(h)
        t = pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU)
        pynvml.nvmlShutdown()
        return {"used": m.used//(1024*1024), "free": m.free//(1024*1024),
                "total": m.total//(1024*1024), "util": u.gpu, "temp": t}
    except:
        return {}

def ollama_status():
    try:
        r = urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3)
        d = json.loads(r.read().decode())
        return {"up": True, "models": [m["name"] for m in d.get("models",[])], "count": len(d.get("models",[]))}
    except:
        return {"up": False, "models": [], "count": 0}

def generate_chart():
    if len(history["t"]) < 2:
        return ""
    fig, axes = plt.subplots(2, 2, figsize=(14, 9), facecolor="#0d1117")
    fig.suptitle("RTX 4060 Laptop GPU — Real-time Metrics", fontsize=13, fontweight="bold", color="#c9d1d9")
    t = list(history["t"])
    for ax in axes.flat:
        ax.set_facecolor("#161b22")
        ax.tick_params(colors="#8b949e", labelsize=8)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        for spine in ax.spines.values():
            spine.set_color("#30363d")
    # Panel 1: Memory Used
    ax = axes[0,0]
    ax.fill_between(t, list(history["mem_used"]), alpha=0.25, color="#58a6ff")
    ax.plot(t, list(history["mem_used"]), color="#58a6ff", linewidth=2)
    ax.axhline(y=8188, color="#484f58", linestyle="--", alpha=0.6, label="Total 8 GB")
    ax.set_ylabel("MiB", color="#8b949e"); ax.set_title("GPU Memory Used", color="#c9d1d9"); ax.legend(loc="upper right", fontsize=7, facecolor="#161b22", edgecolor="#30363d", labelcolor="#c9d1d9")
    # Panel 2: Memory Free
    ax = axes[0,1]
    ax.fill_between(t, list(history["mem_free"]), alpha=0.25, color="#3fb950")
    ax.plot(t, list(history["mem_free"]), color="#3fb950", linewidth=2)
    ax.set_ylabel("MiB", color="#8b949e"); ax.set_title("GPU Memory Free", color="#c9d1d9")
    # Panel 3: GPU Utilization
    ax = axes[1,0]
    ax.fill_between(t, list(history["gpu_util"]), alpha=0.2, color="#d2991d")
    ax.plot(t, list(history["gpu_util"]), color="#d2991d", linewidth=2)
    ax.set_ylim(0, 100); ax.set_ylabel("%", color="#8b949e"); ax.set_title("GPU Utilization", color="#c9d1d9")
    # Panel 4: Temperature
    ax = axes[1,1]
    ax.fill_between(t, list(history["gpu_temp"]), alpha=0.2, color="#f778ba")
    ax.plot(t, list(history["gpu_temp"]), color="#f778ba", linewidth=2)
    ax.axhline(y=80, color="#f85149", linestyle="--", alpha=0.5, label="Throttle 80°C")
    ax.set_ylabel("°C", color="#8b949e"); ax.set_title("GPU Temperature", color="#c9d1d9")
    ax.legend(loc="upper right", fontsize=7, facecolor="#161b22", edgecolor="#30363d", labelcolor="#c9d1d9")
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=90, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode()

@app.get("/", response_class=HTMLResponse)
async def index():
    g = gpu_snapshot()
    o = ollama_status()
    now = datetime.now()
    history["t"].append(now)
    history["mem_used"].append(g.get("used", 0))
    history["mem_free"].append(g.get("free", 0))
    history["gpu_util"].append(g.get("util", 0))
    history["gpu_temp"].append(g.get("temp", 0))
    history["models"].append(o["count"])
    chart = generate_chart()
    mem_pct = g.get("used", 0) / 8188 * 100
    bar_color = "#3fb950" if mem_pct < 50 else "#d2991d" if mem_pct < 85 else "#f85149"
    temp_color = "#3fb950" if g.get("temp", 0) < 60 else "#d2991d" if g.get("temp", 0) < 80 else "#f85149"
    model_list = ", ".join(o["models"]) if o["models"] else "—"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta http-equiv="refresh" content="3">
<title>AI Infra Gateway — Live Dashboard</title>
<style>
  *{{margin:0;padding:0;box-sizing:border-box;}}
  body{{background:#0d1117;color:#c9d1d9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:24px;}}
  .header{{display:flex;justify-content:space-between;align-items:center;margin-bottom:24px;border-bottom:1px solid #30363d;padding-bottom:16px;}}
  .header h1{{font-size:1.4em;color:#58a6ff;font-weight:600;}}
  .header .time{{font-size:0.8em;color:#8b949e;}}
  .badge{{display:inline-flex;align-items:center;gap:6px;background:#161b22;border:1px solid #30363d;border-radius:6px;padding:4px 12px;font-size:0.8em;}}
  .badge .dot{{width:8px;height:8px;border-radius:50%;}}
  .dot-up{{background:#3fb950;box-shadow:0 0 6px #3fb950;}}
  .dot-down{{background:#f85149;box-shadow:0 0 6px #f85149;}}
  .grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:24px;}}
  .card{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:20px;text-align:center;}}
  .card .label{{font-size:0.75em;color:#8b949e;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:10px;}}
  .card .value{{font-size:2.2em;font-weight:700;}}
  .progress-bar{{width:100%;height:6px;background:#21262d;border-radius:3px;overflow:hidden;margin-top:12px;}}
  .progress-fill{{height:100%;border-radius:3px;transition:width 0.5s;}}
  .info-row{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:24px;}}
  .chart-container{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:12px;}}
  .chart-container img{{width:100%;border-radius:6px;}}
  .footer{{text-align:center;color:#484f58;font-size:0.7em;margin-top:24px;padding-top:16px;border-top:1px solid #21262d;}}
  .tag{{display:inline-block;background:#0d419d;color:#58a6ff;border-radius:4px;padding:2px 8px;font-size:0.75em;margin:2px;}}
</style>
</head>
<body>

<div class="header">
  <div>
    <h1>🖥️ AI Infra Inference Gateway</h1>
    <div style="margin-top:4px;display:flex;gap:12px;">
      <span class="badge"><span class="dot {'dot-up' if o['up'] else 'dot-down'}"></span> Ollama {'Online' if o['up'] else 'Offline'}</span>
      <span class="badge"><span class="dot dot-up"></span> Gateway :8000</span>
      <span class="badge"><span class="dot dot-up"></span> Dashboard :9090</span>
    </div>
  </div>
  <div class="time">{now.strftime('%Y-%m-%d %H:%M:%S')}</div>
</div>

<div class="grid">
  <div class="card">
    <div class="label">GPU Memory Used</div>
    <div class="value" style="color:{bar_color}">{g.get('used',0):,}</div>
    <div style="font-size:0.8em;color:#8b949e;margin-top:4px">MiB / 8,188 MiB</div>
    <div class="progress-bar"><div class="progress-fill" style="width:{mem_pct:.1f}%;background:{bar_color}"></div></div>
    <div style="font-size:0.7em;color:#8b949e;margin-top:6px">{mem_pct:.1f}%</div>
  </div>
  <div class="card">
    <div class="label">GPU Memory Free</div>
    <div class="value" style="color:#3fb950">{g.get('free',0):,}</div>
    <div style="font-size:0.8em;color:#8b949e;margin-top:8px">MiB Available</div>
  </div>
  <div class="card">
    <div class="label">GPU Utilization</div>
    <div class="value" style="color:#d2991d">{g.get('util',0)}%</div>
    <div style="font-size:0.8em;color:#8b949e;margin-top:8px">CUDA Cores</div>
  </div>
  <div class="card">
    <div class="label">GPU Temperature</div>
    <div class="value" style="color:{temp_color}">{g.get('temp','—')}°C</div>
    <div style="font-size:0.8em;color:#8b949e;margin-top:8px">Safe < 80°C</div>
  </div>
</div>

<div class="info-row">
  <div class="card">
    <div class="label">Loaded Models</div>
    <div class="value" style="font-size:1.5em">{o['count']}</div>
    <div style="margin-top:8px;max-height:60px;overflow-y:auto;">
      <span class="tag">{model_list}</span>
    </div>
  </div>
  <div class="card">
    <div class="label">Gateway Status</div>
    <div style="font-size:1.1em;color:#3fb950;margin-top:10px">● Online</div>
    <div style="font-size:0.75em;color:#8b949e;margin-top:6px">http://localhost:8000</div>
  </div>
  <div class="card">
    <div class="label">Data Points</div>
    <div class="value" style="font-size:1.5em">{len(history['t'])}</div>
    <div style="font-size:0.75em;color:#8b949e;margin-top:6px">of {MAX_POINTS} samples</div>
  </div>
</div>

<div class="chart-container">
  <img src="data:image/png;base64,{chart}" alt="GPU Metrics">
</div>

<div class="footer">
  AI Infra Gateway v2.0 | NVIDIA RTX 4060 Laptop | Auto-refresh 3s | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
</div>

</body>
</html>"""

if __name__ == "__main__":
    def collector():
        while True:
            g = gpu_snapshot()
            o = ollama_status()
            history["t"].append(datetime.now())
            history["mem_used"].append(g.get("used", 0))
            history["mem_free"].append(g.get("free", 0))
            history["gpu_util"].append(g.get("util", 0))
            history["gpu_temp"].append(g.get("temp", 0))
            history["models"].append(o["count"])
            time.sleep(3)
    threading.Thread(target=collector, daemon=True).start()
    print("=" * 50)
    print("  AI Infra Dashboard v2")
    print("  http://localhost:9090")
    print("  Professional dark theme")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=9090, log_level="warning")