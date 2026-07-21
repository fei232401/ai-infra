# 02 — GPU Real-Time Monitoring Dashboard

Dark-themed professional GPU monitoring dashboard with zero external dependencies.

## Files

| File | Purpose |
|------|---------|
| `dashboard_v2.py` | Current version: dark theme + 4-panel line charts + 3s auto-refresh |
| `dashboard.py` | Initial version (retained for reference) |

## Quick Start

```powershell
cd 02-dashboard
python dashboard_v2.py
# Open → http://localhost:9090
```

## Tech Stack

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| GPU Sampling | pynvml 11.0+ | NVIDIA driver-level register reading — memory, utilization, temperature |
| Visualization | matplotlib (Agg backend) | Headless rendering to base64 PNG, embedded directly in HTML |
| Web Server | FastAPI | Serves HTML + auto-refresh via HTTP meta tag |
| Theme | Custom CSS (`#0d1117`) | GitHub Dark Mode inspired, professional appearance |

## Monitoring Panels

| Panel | Metric | Unit |
|-------|--------|------|
| Top-Left | GPU Memory Used | MiB |
| Top-Right | GPU Memory Free | MiB |
| Bottom-Left | GPU Utilization | % |
| Bottom-Right | GPU Temperature | °C |

## Data Pipeline

```
pynvml.nvmlInit()
  → nvmlDeviceGetHandleByIndex(0)
  → nvmlDeviceGetMemoryInfo(handle)        # VRAM used/free
  → nvmlDeviceGetUtilizationRates(handle)   # GPU %
  → nvmlDeviceGetTemperature(handle)        # °C
  → append to deque<120> (6-minute window)
  → matplotlib renders 4-panel PNG (Agg backend, non-blocking)
  → base64 encode → embed in HTML <img> tag
  → HTTP meta refresh every 3 seconds
```

## Design Decisions

### Why Zero External Dependencies?

The primary constraint is Windows bare metal with no Docker/K8s/WSL2. Prometheus + Grafana would require additional services that aren't available. pynvml reads directly from the NVIDIA driver — no middleware, no daemon, no network. This is "monitoring that works where Prometheus can't reach."

### Why matplotlib Agg Backend Instead of a Web Charting Library?

The Agg (Anti-Grain Geometry) backend renders charts to memory buffers without a display server. This avoids Tkinter/GUI dependencies on Windows and produces PNG data that can be inlined as base64 — zero extra HTTP requests, zero JavaScript dependencies.

### Why 3-Second Refresh Instead of WebSocket Push?

For a single-node monitoring dashboard, HTTP meta refresh is sufficient. WebSocket would add connection management complexity for a use case that only needs periodic snapshots. The dashboard is a diagnostic tool, not a real-time alerting system.

### Why 120-Data-Point Window (6 Minutes)?

Balances trend visibility with memory efficiency. 120 points at 3-second intervals provides enough history to spot memory leaks or temperature spikes while keeping the HTML payload under 500KB.