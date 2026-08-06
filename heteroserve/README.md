# HeteroServe v3 — 服务面 / 缓存中心

> 面向 **8GB 单卡** 的云原生 LLM Serving 优化平台。核心 = **LMCache 分层 KV 缓存**（GPU hot → CPU warm → SSD cold）+ vLLM 推理调优。
> 与 CyberRouter v2（控制面调度器）构成完整闭环：**HeteroServe 选"tier 内如何高效运行"，CyberRouter 选"请求去哪"**。

## 核心数字（全部真实测量，2026-08-06）

| 指标 | 结果 |
|---|---|
| LMCache 前缀复用 TTFT | 逐出前缀恢复 **3–5×** 提速（0.60s → 0.11–0.19s） |
| LMCache 命中率（互异前缀负载） | **40.5%**（基准负载） |
| 调参矩阵推荐配置 | **gpu=0.8 / max_num_seqs=16 / cpu_cache=1GB** |
| 推荐配置相对旧 BASE | QPS **2.45×**（4.17→10.23），TTFT P95 **降 5.3×**（3.59s→0.68s） |
| GPU KV 容量 | gpu 0.55→0.8 后 **65K→123K tokens 翻倍** |

## 架构

```
GPU tier:  vLLM 3B-AWQ + LMCache Connector
           ├── GPU KV (hot, 123K tokens)   ← vLLM 管
           ├── CPU 缓存 (warm, 1GB)         ← LMCache 卸载
           └── SSD 缓存 (cold, 10GB)        ← LMCache 卸载
CPU tier:  Ollama 1.5b / 3b（调度器第二目标，成本近零）
```

集成点：vLLM 镜像内置 LMCache，接入 = `LMCACHE_CONFIG_FILE` + `--kv-transfer-config '{"kv_connector":"LMCacheConnectorV1","kv_role":"kv_both"}'`。

## 与 CyberRouter 的联动（旗舰闭环）

```
LMCache 命中率 / GPU 水位 (vllm:prefix_cache_* / num_requests_running)
   │  Operator 5s 采集 (internal/metrics)
   ▼
路由快照.tiers 动态状态 (命中率/预判P99)
   │  gateway 决策时读取
   ▼
命中率高 → 便宜 tier 权重上调 (cache-aware, 规则条件)
GPU 忙 (预判P99> SLO) → 主动降级 CPU tier (预判性降级)
```

**Phase 1 的缓存指标直接喂给了 Phase 2/3 的调度决策**——这是两个项目的唯一、最闪的联结点（BLUEPRINT §4.5）。

## Benchmark 小结

### 1. 前缀复用 benchmark（LMCache 价值证明）
- 工具：`benchmark/benchmark.py`，场景 = 20 个互异 4000-token 前缀（80K > GPU KV 65K，**必须撑爆才能触发逐出**）
- 流程：cold 全部前缀撑满 GPU KV → **反向 warm**（保证 prefix=0 已被逐出）
- 结果：被逐出前缀 warm TTFT 0.60s（重新 prefill）→ LMCache 0.11–0.19s（CPU/SSD 恢复）；未逐出两者均 0.04s
- **诚实口径**：LMCache 增量价值在"GPU KV 超容量"场景，不是替代 vLLM 的 GPU 缓存

### 2. 调参矩阵（H2，全自动）
- 工具：`benchmark/tuning_matrix.py`（orchestrator）+ `benchmark/load_test.py`（并发压测）
- 扫描维度：`gpu_memory_utilization`（0.7/0.8/0.9）× `max_num_seqs`（4/8/16/32）× `max_local_cpu_size`（1/2/4GB）
- 结果：`benchmark/results/matrix_results.json`

| 系列 | 结论 |
|---|---|
| gpu_memory_utilization | **0.8 最优**（0.9 OOM：LMCache 连接器被挤爆）|
| max_num_seqs | **16 峰值**（4 是瓶颈，32 饱和回落）|
| max_local_cpu_size | **1GB 已够**（当前负载；4GB 在 6Gi limit 下 OOM）|

**踩坑记录**（面试/复盘必读）：
1. **结果文件相对路径**：benchmark 结果必须写 `results/` 子目录，否则 orchestrator 找不到会回退读旧文件 → 数据串档
2. **逐出触发条件随配置漂移**：80K > 65K 的前提绑定 gpu=0.55；gpu=0.8 后 KV=123K 装得下 80K，逐出不触发，LMCache 测不到——**场景参数必须和配置绑定**
3. **单 GPU 滚动更新死锁**：deployment 必须用 Recreate（见 vllm-3b.yaml），orchestrator 用 `kubectl delete pod` 强制重建

## 目录结构

```
heteroserve/
├── README.md                ← 本文件
├── config/lmcache.yaml      ← LMCache 分层缓存配置（CPU/SSD 上限）
└── benchmark/
    ├── benchmark.py         ← 前缀复用 benchmark（LMCache 价值证明）
    ├── load_test.py         ← 并发吞吐压测器（QPS/TTFT P50/95/99）
    ├── tuning_matrix.py     ← 调参矩阵 orchestrator（全自动扫配置空间）
    └── results/             ← 全部真实测量结果（JSON）
```

## 复现命令

```bash
# 前缀复用 benchmark
python3 benchmark/benchmark.py --url http://localhost:8000/v1 \
    --model /models/qwen2.5-3b-awq --num-prefixes 20 --prefix-tokens 4000

# 调参矩阵（自动改配置→重启→压测→恢复 BASE）
python3 benchmark/tuning_matrix.py
```
