#!/usr/bin/env python3
"""
Prometheus Exporter - 暴露 GPU + Ollama 指标给 ECS K3s 抓取

庖丁解牛 - Prometheus Exporter：
类比：就像一个"体检报告生成器"
- 每当 Prometheus 来敲门（HTTP GET /metrics）
- Exporter 就立刻给 GPU 和 Ollama 做一次全面体检
- 把心跳/血压/体温（显存/利用率/模型状态）用 Prometheus 标准格式输出
- Prometheus 每 15 秒来取一次报告，存进时序数据库
- Grafana 再从 Prometheus 读取数据画成仪表盘

你的 ECS K3s 上已部署 Prometheus + Grafana
只需在此 Exporter 暴露 /metrics 端点
ECS 端配置 scrape target 指向你本机的 <ip>:9090 即可
"""
import time
import json
import os
import sys
import subprocess
import logging
from typing import Optional

import pynvml
from prometheus_client import Gauge, Info, start_http_server, CollectorRegistry, generate_latest
from prometheus_client.core import GaugeMetricFamily

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger("exporter")

# ============ 指标定义 ============
# GPU 指标
gpu_memory_used = Gauge("ai_infra_gpu_memory_used_mb", "GPU 显存已用量 (MiB)", ["gpu_id"])
gpu_memory_free = Gauge("ai_infra_gpu_memory_free_mb", "GPU 显存空闲量 (MiB)", ["gpu_id"])
gpu_memory_total = Gauge("ai_infra_gpu_memory_total_mb", "GPU 显存总量 (MiB)", ["gpu_id"])
gpu_utilization = Gauge("ai_infra_gpu_utilization_pct", "GPU 利用率 (%)", ["gpu_id"])
gpu_temperature = Gauge("ai_infra_gpu_temperature_c", "GPU 温度 (Celsius)", ["gpu_id"])

# Ollama 指标
ollama_models_loaded = Gauge("ai_infra_ollama_models_loaded", "已加载模型数")
ollama_service_up = Gauge("ai_infra_ollama_service_up", "Ollama 服务是否可达 (1=可达, 0=不可达)")

# 推理性能 (从网关日志或压测脚本采集)
inference_ttft_ms = Gauge("ai_infra_inference_ttft_ms", "首字延迟 (ms)", ["model"])
inference_tpot_ms = Gauge("ai_infra_inference_tpot_ms", "每 token 延迟 (ms)", ["model"])
inference_throughput_tps = Gauge("ai_infra_inference_throughput_tps", "推理吞吐 (tokens/s)", ["model"])

# ============ 采集函数 ============
def collect_gpu_metrics():
    """采集 GPU 实时指标"""
    try:
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)

        mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
        util = pynvml.nvmlDeviceGetUtilizationRates(handle)
        temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)

        gpu_id = "0"
        gpu_memory_used.labels(gpu_id=gpu_id).set(mem.used // (1024 * 1024))
        gpu_memory_free.labels(gpu_id=gpu_id).set(mem.free // (1024 * 1024))
        gpu_memory_total.labels(gpu_id=gpu_id).set(mem.total // (1024 * 1024))
        gpu_utilization.labels(gpu_id=gpu_id).set(util.gpu)
        gpu_temperature.labels(gpu_id=gpu_id).set(temp)

        pynvml.nvmlShutdown()
        return True
    except Exception as e:
        logger.error(f"GPU 采集失败: {e}")
        return False

def collect_ollama_metrics():
    """采集 Ollama 服务状态和模型信息"""
    try:
        import urllib.request
        # 1. 服务可达性
        resp = urllib.request.urlopen("http://localhost:11434/api/tags", timeout=5)
        data = json.loads(resp.read().decode())
        ollama_service_up.set(1)
        models = data.get("models", [])
        ollama_models_loaded.set(len(models))
        return models
    except Exception as e:
        ollama_service_up.set(0)
        ollama_models_loaded.set(0)
        logger.warning(f"Ollama 采集失败: {e}")
        return []

# ============ 主循环 ============
def main():
    port = int(os.environ.get("EXPORTER_PORT", "9090"))
    logger.info(f"启动 Prometheus Exporter 在端口 {port}")
    logger.info(f"指标端点: http://localhost:{port}/metrics")
    logger.info(f"ECS K3s 配置: scrape target = <你的本机IP>:{port}")

    start_http_server(port)
    logger.info("Exporter 已就绪，等待 Prometheus 抓取...")

    # 保持运行，prometheus_client 库在后台自动响应 /metrics
    # 这里用一个简单循环做主动指标更新
    import threading
    import time

    def update_loop():
        while True:
            collect_gpu_metrics()
            collect_ollama_metrics()
            time.sleep(15)  # 每15秒更新一次指标

    updater = threading.Thread(target=update_loop, daemon=True)
    updater.start()

    # 主线程等待
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Exporter 关闭")

if __name__ == "__main__":
    main()