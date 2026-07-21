"""Grafana 原生 Windows 安装脚本 — 从 Grafana CDN 下载"""
import urllib.request
import zipfile
import os
import sys
import shutil

MONITORING = r"C:\Users\admin\Desktop\ai-infra-gateway\monitoring"
GRAFANA_ZIP = os.path.join(MONITORING, "grafana.zip")
GRAFANA_DIR = os.path.join(MONITORING, "grafana")
GRAFANA_EXE = os.path.join(GRAFANA_DIR, "bin", "grafana-server.exe")
URL = "https://dl.grafana.com/enterprise/release/grafana-enterprise-11.3.0.windows-amd64.zip"

os.makedirs(MONITORING, exist_ok=True)

# Step 1: Check if already installed
if os.path.exists(GRAFANA_EXE):
    print(f"[OK] Grafana already installed at: {GRAFANA_EXE}")
    sys.exit(0)

# Step 2: Download
if not os.path.exists(GRAFANA_ZIP) or os.path.getsize(GRAFANA_ZIP) < 10*1024*1024:
    print(f"Downloading Grafana from {URL}...")
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=600) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        with open(GRAFANA_ZIP, "wb") as f:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded / total * 100
                    print(f"  {pct:.0f}% ({downloaded/(1024*1024):.0f}/{total/(1024*1024):.0f} MB)", end="\r")
    print(f"\nDownloaded: {downloaded/(1024*1024):.1f} MB")
else:
    print(f"ZIP exists: {os.path.getsize(GRAFANA_ZIP)/(1024*1024):.1f} MB")

# Step 3: Extract
if not os.path.exists(GRAFANA_EXE):
    print("Extracting Grafana...")
    with zipfile.ZipFile(GRAFANA_ZIP, "r") as zf:
        top_dir = zf.namelist()[0].split("/")[0]
        zf.extractall(MONITORING)
    extracted = os.path.join(MONITORING, top_dir)
    if os.path.exists(extracted):
        if os.path.exists(GRAFANA_DIR):
            shutil.rmtree(GRAFANA_DIR, ignore_errors=True)
        os.rename(extracted, GRAFANA_DIR)
    # Clean up zip
    os.remove(GRAFANA_ZIP)
    print(f"Extracted to: {GRAFANA_DIR}")

# Step 4: Verify
if os.path.exists(GRAFANA_EXE):
    print(f"\n[OK] Grafana ready!")
    print(f"  Binary: {GRAFANA_EXE}")
    print(f"\n  Start command:")
    print(f'  "{GRAFANA_EXE}" --homepath="{GRAFANA_DIR}"')
    print(f"\n  Default login: admin/admin")
    print(f"  URL: http://localhost:3000")
else:
    print("[FAIL] Grafana installation failed")
    sys.exit(1)