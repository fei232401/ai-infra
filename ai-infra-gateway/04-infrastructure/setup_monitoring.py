"""下载并配置本地 Prometheus + Grafana"""
import urllib.request
import zipfile
import tarfile
import os
import sys
import subprocess
import shutil

MONITORING_DIR = r"C:\Users\admin\Desktop\ai-infra-gateway\monitoring"
os.makedirs(MONITORING_DIR, exist_ok=True)

# Prometheus 2.55.0 Windows AMD64
PROM_URL = "https://github.com/prometheus/prometheus/releases/download/v2.55.0/prometheus-2.55.0.windows-amd64.zip"
PROM_ZIP = os.path.join(MONITORING_DIR, "prometheus.zip")
PROM_DIR = os.path.join(MONITORING_DIR, "prometheus")

# Grafana 11.3.0 Windows AMD64
GRAFANA_URL = "https://dl.grafana.com/enterprise/release/grafana-enterprise-11.3.0.windows-amd64.zip"
GRAFANA_ZIP = os.path.join(MONITORING_DIR, "grafana.zip")
GRAFANA_DIR = os.path.join(MONITORING_DIR, "grafana")

def download_file(url, dest, name):
    """带进度下载"""
    if os.path.exists(dest) and os.path.getsize(dest) > 1000000:
        print(f"[{name}] 已存在，跳过下载")
        return True

    print(f"[{name}] 下载中: {url}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=600) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            with open(dest, "wb") as f:
                while True:
                    chunk = resp.read(1024*1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        pct = downloaded / total * 100
                        mb = downloaded / (1024*1024)
                        total_mb = total / (1024*1024)
                        print(f"  {pct:.0f}% ({mb:.1f}/{total_mb:.1f} MB)", end="\r")
            print(f"\n[{name}] 下载完成: {downloaded/(1024*1024):.1f} MB")
            return True
    except Exception as e:
        print(f"\n[{name}] 下载失败: {e}")
        return False

def extract_zip(zip_path, dest_dir, name):
    """解压并去掉顶层目录"""
    if os.path.exists(dest_dir):
        print(f"[{name}] 已解压，跳过")
        return True

    print(f"[{name}] 解压中...")
    with zipfile.ZipFile(zip_path, "r") as zf:
        # 获取顶层目录名
        top_dir = zf.namelist()[0].split("/")[0]
        zf.extractall(MONITORING_DIR)

    # 重命名
    extracted = os.path.join(MONITORING_DIR, top_dir)
    if os.path.exists(extracted) and not os.path.exists(dest_dir):
        os.rename(extracted, dest_dir)
    print(f"[{name}] 解压完成: {dest_dir}")
    return True

def write_prometheus_config():
    """生成 prometheus.yml 配置"""
    config = os.path.join(PROM_DIR, "prometheus.yml")
    if os.path.exists(config):
        os.remove(config)

    config_content = """# AI Infra 本地 Prometheus 配置
# 抓取 Exporter 的 GPU + Ollama 指标

global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: "ai-infra-gateway"
    metrics_path: /metrics
    static_configs:
      - targets: ["localhost:9090"]
        labels:
          project: "ai-infra-gateway"
          env: "windows-local"
"""
    with open(config, "w", encoding="utf-8") as f:
        f.write(config_content)
    print(f"[Prometheus] 配置已写入: {config}")

def write_grafana_config():
    """Grafana 默认配置可用，不做修改"""
    pass

def main():
    print("=" * 60)
    print("  本地监控栈部署: Prometheus + Grafana")
    print("=" * 60)

    # 1. 下载 Prometheus
    if download_file(PROM_URL, PROM_ZIP, "Prometheus"):
        extract_zip(PROM_ZIP, PROM_DIR, "Prometheus")
        write_prometheus_config()

    # 2. 下载 Grafana
    if download_file(GRAFANA_URL, GRAFANA_ZIP, "Grafana"):
        extract_zip(GRAFANA_ZIP, GRAFANA_DIR, "Grafana")

    # 3. 打印启动命令
    print("\n" + "=" * 60)
    print("  启动命令")
    print("=" * 60)
    print(f"  1. 启动 Exporter:")
    print(f"     cd {os.path.dirname(os.path.dirname(__file__))}")
    print(f"     python scripts\\metrics_exporter.py")
    print(f"\n  2. 启动 Prometheus:")
    prom_exe = os.path.join(PROM_DIR, "prometheus.exe")
    if os.path.exists(prom_exe):
        print(f"     {prom_exe} --config.file={os.path.join(PROM_DIR,'prometheus.yml')}")
    print(f"\n  3. 启动 Grafana:")
    grafana_exe = os.path.join(GRAFANA_DIR, "bin", "grafana-server.exe")
    if os.path.exists(grafana_exe):
        print(f"     {grafana_exe} --homepath={GRAFANA_DIR}")
    print(f"\n  4. Grafana 默认登录: admin/admin")
    print(f"     URL: http://localhost:3000")

    print("\n  ==============================")
    print("  Done. 监控栈部署脚本执行完毕。")
    print("  ==============================")

if __name__ == "__main__":
    main()