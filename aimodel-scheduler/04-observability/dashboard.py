"""
Scheduler Dashboard — real-time visualization of the AI Model Scheduler state.

Features:
  - Backend health status (4 panels)
  - Routing strategy distribution pie chart
  - Latency trend line chart
  - Request throughput bar chart

Run: python dashboard.py  (serves on http://localhost:9010)

Reuses the same pynvml + matplotlib approach as AI Infra Gateway's dashboard.
No external monitoring infrastructure required.
"""

import asyncio
import base64
import io
import json
import logging
import time
from collections import defaultdict
from typing import Dict, List

import aiohttp
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] Dashboard: %(message)s",
)
logger = logging.getLogger("dashboard")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SCHEDULER_URL = "http://localhost:9000"
POLL_INTERVAL = 3  # seconds between status fetches

app = FastAPI(title="Scheduler Dashboard", version="1.0.0")

# In-memory time-series data
history: List[dict] = []
MAX_HISTORY = 100


# ---------------------------------------------------------------------------
# Data Collection
# ---------------------------------------------------------------------------

async def fetch_scheduler_status() -> dict:
    """Fetch current status from the scheduler API."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{SCHEDULER_URL}/v1/status", timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
    except Exception as e:
        logger.warning(f"Failed to fetch status: {e}")
    return {"error": "Scheduler unreachable"}


async def poll_loop():
    """Background task to continuously poll scheduler status."""
    global history
    while True:
        status = await fetch_scheduler_status()
        status["_timestamp"] = time.time()
        history.append(status)
        if len(history) > MAX_HISTORY:
            history = history[-MAX_HISTORY:]
        await asyncio.sleep(POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Dashboard HTML
# ---------------------------------------------------------------------------

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta http-equiv="refresh" content="5">
<title>AI Model Scheduler — Dashboard</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Segoe UI', Consolas, monospace; background: #0d1117; color: #c9d1d9; padding: 20px; }
  h1 { color: #58a6ff; margin-bottom: 10px; font-size: 24px; }
  .subtitle { color: #8b949e; margin-bottom: 20px; font-size: 14px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(400px, 1fr)); gap: 20px; }
  .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; }
  .card h3 { color: #f0f6fc; margin-bottom: 12px; font-size: 16px; border-bottom: 1px solid #30363d; padding-bottom: 8px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; color: #8b949e; padding: 6px 8px; border-bottom: 1px solid #30363d; }
  td { padding: 6px 8px; border-bottom: 1px solid #21262d; }
  .healthy { color: #3fb950; }
  .unhealthy { color: #f85149; }
  .unknown { color: #d2991d; }
  .open-circuit { color: #f85149; font-weight: bold; }
  .closed-circuit { color: #3fb950; }
  .half-open-circuit { color: #d2991d; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: bold; }
  .badge-ollama { background: #1f6feb; color: white; }
  .badge-vllm { background: #6e40c9; color: white; }
  .badge-mock { background: #b62324; color: white; }
  .stat { font-size: 28px; font-weight: bold; }
  .stat-label { font-size: 12px; color: #8b949e; }
  .charts { margin-top: 20px; }
  .charts img { max-width: 100%; border-radius: 4px; border: 1px solid #30363d; }
  .refresh { color: #484f58; font-size: 11px; float: right; }
</style>
</head>
<body>
  <h1>🧠 AI Model Scheduler Dashboard <span class="refresh">auto-refresh 5s</span></h1>
  <p class="subtitle">Heterogeneous Inference Routing · Real-time Monitor</p>

  <div class="grid" id="stats-grid">
    <div class="card">
      <h3>📊 Throughput Stats</h3>
      <div class="stat" id="total-requests">--</div>
      <div class="stat-label">Total Requests</div>
      <br>
      <div class="stat healthy" id="success-rate">--</div>
      <div class="stat-label">Success Rate</div>
    </div>
    <div class="card">
      <h3>⏱️ Scheduler Uptime</h3>
      <div class="stat" id="uptime">--</div>
      <div class="stat-label">Seconds Running</div>
      <br>
      <div class="stat" id="sessions">--</div>
      <div class="stat-label">Active Sessions</div>
    </div>
  </div>

  <div class="grid" style="margin-top: 20px;">
    <div class="card">
      <h3>🔌 Backend Status</h3>
      <table id="backend-table">
        <thead><tr><th>Backend</th><th>Engine</th><th>Health</th><th>Circuit</th><th>TTFT</th><th>TPS</th><th>Active</th><th>Errors</th></tr></thead>
        <tbody></tbody>
      </table>
    </div>
    <div class="card">
      <h3>🛤️ Routing Distribution</h3>
      <div id="routing-dist"></div>
    </div>
  </div>

  <div class="charts">
    <div class="card">
      <h3>📈 Latency Trend (TTFT)</h3>
      <img id="latency-chart" src="/api/chart/latency" alt="Latency Trend">
    </div>
    <div class="card">
      <h3>📊 Throughput by Backend</h3>
      <img id="throughput-chart" src="/api/chart/throughput" alt="Throughput">
    </div>
  </div>

  <script>
    async function refresh() {
      try {
        const resp = await fetch('/api/data');
        const data = await resp.json();

        // Stats
        const s = data.scheduler || {};
        document.getElementById('total-requests').textContent = s.total_requests || 0;
        document.getElementById('success-rate').textContent =
          ((s.success_rate || 0) * 100).toFixed(1) + '%';
        document.getElementById('uptime').textContent =
          (s.uptime_seconds || 0).toFixed(0) + 's';
        document.getElementById('sessions').textContent = s.active_sessions || 0;

        // Backend table
        const tbody = document.querySelector('#backend-table tbody');
        tbody.innerHTML = '';
        (data.backends || []).forEach(b => {
          const healthClass = b.health === 'healthy' ? 'healthy' : (b.health === 'unhealthy' ? 'unhealthy' : 'unknown');
          const cClass = b.circuit_state === 'open' ? 'open-circuit' : (b.circuit_state === 'half_open' ? 'half-open-circuit' : 'closed-circuit');
          const engineBadge = b.engine === 'ollama' ? 'badge-ollama' : (b.engine === 'vllm' ? 'badge-vllm' : 'badge-mock');
          tbody.innerHTML += `<tr>
            <td><strong>${b.id}</strong></td>
            <td><span class="badge ${engineBadge}">${b.engine}</span></td>
            <td class="${healthClass}">● ${b.health}</td>
            <td class="${cClass}">${b.circuit_state}</td>
            <td>${(b.score?.ttft_ms || 0).toFixed(1)}ms</td>
            <td>${(b.score?.throughput_tps || 0).toFixed(1)}</td>
            <td>${b.active_requests || 0}/${b.max_concurrency}</td>
            <td>${b.score?.error_count || 0}</td>
          </tr>`;
        });
      } catch(e) { console.error(e); }
    }

    setInterval(refresh, 3000);
    refresh();
  </script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@app.get("/")
async def index():
    """Dashboard home page."""
    return HTMLResponse(DASHBOARD_HTML)


@app.get("/api/data")
async def api_data():
    """Return the latest scheduler status as JSON."""
    if not history:
        status = await fetch_scheduler_status()
    else:
        status = history[-1]
    return JSONResponse(status)


@app.get("/api/chart/latency")
async def chart_latency():
    """Generate latency trend chart."""
    if not HAS_MATPLOTLIB:
        return HTMLResponse("<p>matplotlib not installed</p>")

    if len(history) < 2:
        return HTMLResponse("<p>Not enough data yet...</p>")

    fig, ax = plt.subplots(figsize=(10, 3), facecolor="#0d1117")
    ax.set_facecolor("#0d1117")

    timestamps = [h["_timestamp"] for h in history]
    t0 = timestamps[0]

    backend_data = {}
    for h in history:
        backends = h.get("backends", [])
        for b in backends:
            bid = b["id"]
            ttft = b.get("score", {}).get("ttft_ms", 0)
            if bid not in backend_data:
                backend_data[bid] = {"x": [], "y": []}
            backend_data[bid]["x"].append(h["_timestamp"] - t0)
            backend_data[bid]["y"].append(ttft)

    colors = ["#58a6ff", "#3fb950", "#d2991d", "#f85149"]
    for i, (bid, data) in enumerate(backend_data.items()):
        if data["x"]:
            ax.plot(data["x"], data["y"], label=bid, color=colors[i % len(colors)], linewidth=1.5)

    ax.set_xlabel("Time (s)", color="#8b949e")
    ax.set_ylabel("TTFT (ms)", color="#8b949e")
    ax.set_title("TTFT Trend by Backend", color="#f0f6fc", fontsize=12)
    ax.legend(loc="upper left", fontsize=8, facecolor="#161b22", edgecolor="#30363d", labelcolor="#c9d1d9")
    ax.tick_params(colors="#8b949e", labelsize=8)
    ax.spines[:].set_color("#30363d")
    ax.grid(True, alpha=0.2, color="#30363d")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=80, bbox_inches="tight", facecolor="#0d1117")
    plt.close(fig)
    buf.seek(0)
    img_base64 = base64.b64encode(buf.read()).decode()
    return HTMLResponse(f'<img src="data:image/png;base64,{img_base64}" style="max-width:100%">')


@app.get("/api/chart/throughput")
async def chart_throughput():
    """Generate throughput bar chart."""
    if not HAS_MATPLOTLIB:
        return HTMLResponse("<p>matplotlib not installed</p>")

    if not history:
        return HTMLResponse("<p>No data yet...</p>")

    latest = history[-1]
    backends = latest.get("backends", [])

    if not backends:
        return HTMLResponse("<p>No backend data</p>")

    fig, ax = plt.subplots(figsize=(10, 3), facecolor="#0d1117")
    ax.set_facecolor("#0d1117")

    ids = [b["id"] for b in backends]
    tps_values = [b.get("score", {}).get("throughput_tps", 0) for b in backends]
    ttft_values = [b.get("score", {}).get("ttft_ms", 0) for b in backends]

    x = range(len(ids))
    width = 0.35
    bars1 = ax.bar([i - width / 2 for i in x], tps_values, width, label="Throughput (t/s)", color="#58a6ff")
    ax2 = ax.twinx()
    bars2 = ax2.bar([i + width / 2 for i in x], ttft_values, width, label="TTFT (ms)", color="#d2991d")

    ax.set_xticks(x)
    ax.set_xticklabels(ids, fontsize=8, color="#c9d1d9")
    ax.set_ylabel("Throughput (t/s)", color="#58a6ff", fontsize=10)
    ax2.set_ylabel("TTFT (ms)", color="#d2991d", fontsize=10)
    ax.set_title("Backend Performance Snapshot", color="#f0f6fc", fontsize=12)
    ax.tick_params(colors="#8b949e", labelsize=8)
    ax2.tick_params(colors="#8b949e", labelsize=8)
    ax.spines[:].set_color("#30363d")
    ax2.spines[:].set_color("#30363d")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=80, bbox_inches="tight", facecolor="#0d1117")
    plt.close(fig)
    buf.seek(0)
    img_base64 = base64.b64encode(buf.read()).decode()
    return HTMLResponse(f'<img src="data:image/png;base64,{img_base64}" style="max-width:100%">')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup():
    asyncio.create_task(poll_loop())
    logger.info("Dashboard started — polling scheduler every %ds", POLL_INTERVAL)


if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("  Scheduler Dashboard")
    logger.info(f"  URL: http://localhost:9010")
    logger.info(f"  Polling: {SCHEDULER_URL}/v1/status every {POLL_INTERVAL}s")
    logger.info("=" * 50)
    uvicorn.run(app, host="127.0.0.1", port=9010, log_level="info")