# AI Infra — Cloud GPU vLLM 压测工作汇报 (SRE-LAB Aligned)

> **执行人**: [姓名]  
> **完成时间**: 2026-06-21 [具体时间]  
> **资源消耗**: AutoDL [GPU型号] × [小时数]  
> **方法对齐**: SRE-LAB 三层场景分层 + 阶梯加压

---

## 一、工作目标

在 AutoDL 云实例上部署 vLLM 推理引擎，按 SRE-LAB 压测方法论执行三层场景分层压测（light/medium/heavy），验证 Continuous Batching + PagedAttention 的 GPU 调度能力，并与本地 Ollama（RTX 4060 Laptop 8GB）基线进行跨平台性能对比。

---

## 二、环境配置

### 租赁实例

| 配置项 | 规格 |
|--------|------|
| GPU | **[填写]** |
| VRAM | **[填写]** |
| CPU / RAM | **[填写]** |
| CUDA | **[填写]** |
| 镜像 | **[填写]** |
| 每小时成本 | **[填写 ¥/h]** |
| 实际使用时长 | **[填写 小时]** |

### 软件版本

| 组件 | 版本 |
|------|------|
| vLLM | **[填写]** |
| Python | **[填写]** |
| PyTorch | **[填写]** |

### 对照环境

| 项目 | Ollama (Local) | vLLM (Cloud) |
|------|---------------|--------------|
| GPU | RTX 4060 Laptop 8GB | **[填写]** |
| VRAM | 8,188 MiB | **[填写]** |
| 平台 | Windows 11 bare metal | Linux (AutoDL) |
| 引擎 | Ollama 0.30.9 | vLLM |

---

## 三、压测方法（SRE-LAB 对齐）

### 场景分层

| 场景 | 权重 | Prompt 长度 | Max Tokens | 验证目标 |
|------|------|------------|------------|---------|
| light_inference | 50% | ~15 tokens | 50 | 基础吞吐量 |
| medium_inference | 30% | ~80 tokens | 200 | 并发处理 |
| heavy_inference | 15% | ~300 tokens | 500 | 极限压力 |
| health_check | 5% | ~5 tokens | 5 | API 健康 |

### 阶梯加压参数

| 参数 | 值 |
|------|-----|
| 起始并发 | 1 |
| 步进增量 | +4 并发/步 |
| 步长时长 | 60s |
| 最大并发 | 32 |
| 每步请求数 | 32 |

---

## 四、测试结论（1 分钟版）

> [一句话总结 vLLM 相对于 Ollama 在各场景下的性能差异]

### 核心数字

| 模型 | 场景 | Ollama (4060 8G) | vLLM (4090 24G) | 提升 |
|------|------|-----------------|-----------------|------|
| 0.5B | C1 吞吐 | 198 t/s | **[填写]** t/s | **[填写]x** |
| 0.5B | C1 TTFT | 3,383 ms | **[填写]** ms | **[填写]x** |
| 1.5B | C1 吞吐 | 132 t/s | **[填写]** t/s | **[填写]x** |
| 1.5B | C1 TTFT | 6,412 ms | **[填写]** ms | **[填写]x** |
| 0.5B | light RPS | — | **[填写]** | — |
| 0.5B | medium RPS | — | **[填写]** | — |
| 0.5B | heavy RPS | — | **[填写]** | — |
| 1.5B | light RPS | — | **[填写]** | — |
| 1.5B | medium RPS | — | **[填写]** | — |
| 1.5B | heavy RPS | — | **[填写]** | — |

### 结论

> [2-3 句话：哪个场景 vLLM 优势最大，哪个场景差距不大，与 Ollama 本地基线对比的核心发现]

---

## 五、完整压测数据

### 5.1 vLLM Step-Pressure 结果

从 `data/benchmark_summary.json` 或 `data/REPORT.md` 粘贴：

**qwen2.5:0.5b — light_inference**
```
[粘贴数据表: Conc | OK% | TTFT | TTFT_P95 | TPOT | TPS | RPS | P99_Tok]
```

**qwen2.5:0.5b — medium_inference**
```
[粘贴数据表]
```

**qwen2.5:0.5b — heavy_inference**
```
[粘贴数据表]
```

**qwen2.5:1.5b — light_inference**
```
[粘贴数据表]
```

**qwen2.5:1.5b — medium_inference**
```
[粘贴数据表]
```

**qwen2.5:1.5b — heavy_inference**
```
[粘贴数据表]
```

### 5.2 SRE-LAB 格式结果（对齐 03-benchmark）

```yaml
压测日期: [填写]
实例: [填写]
模型: qwen2.5:0.5b, qwen2.5:1.5b
加压模式: step-pressure (1→4→8→16→32, 60s/step)
总请求数: [填写]

结果:
  qwen2.5:0.5b:
    light_inference (C16):
      avg: [填写]ms
      P50: [填写]ms
      P90: [填写]ms
      P99: [填写]ms
      RPS: [填写]
      TTFT_avg: [填写]ms
      TPOT_avg: [填写]ms
      
    medium_inference (C16):
      avg: [填写]ms
      P50: [填写]ms
      P90: [填写]ms
      P99: [填写]ms
      RPS: [填写]
      TTFT_avg: [填写]ms
      TPOT_avg: [填写]ms

    heavy_inference (C16):
      avg: [填写]ms
      P50: [填写]ms
      P90: [填写]ms
      P99: [填写]ms
      RPS: [填写]
      TTFT_avg: [填写]ms
      TPOT_avg: [填写]ms

  qwen2.5:1.5b:
    [同上结构]

  Ollama 对照 (RTX 4060 8G, 固定 C1-C8):
    [从 PROJECT_NARRATIVE.md §8 引用或从 03-benchmark 数据引用]

结论: [填写]
```

---

## 六、GPU 调度能力分析

### vLLM 关键特性利用情况

| 特性 | 是否启用 | 对性能影响 |
|------|----------|-----------|
| Continuous Batching | ✅ | [填写：在高并发下如何体现] |
| PagedAttention | ✅ | [填写：KV Cache 管理优势] |
| KV Cache 量化 | [填写] | [填写] |
| Prefix Caching | [填写] | [填写] |
| Chunked Prefill | [填写] | [填写] |

### 场景间性能衰减分析

| 模型 | light→medium 衰减 | medium→heavy 衰减 | 分析 |
|------|------------------|-------------------|------|
| 0.5B | **[填写]%** | **[填写]%** | [解释] |
| 1.5B | **[填写]%** | **[填写]%** | [解释] |

> Continuous Batching 在不同 Prompt 长度下的效果分析：短 prompt（light）受益于高批处理密度，长 prompt（heavy）受限于 KV Cache 容量和单个请求的 GPU 占用。

### 吞吐拐点对比

| 模型 | Ollama 拐点 (C1-C8) | vLLM 拐点 (step-pressure) | 分析 |
|------|-------------------|--------------------------|------|
| 0.5B | C2 | **[填写]** | [解释 Continuous Batching 如何推迟拐点] |
| 1.5B | C8 | **[填写]** | [解释] |

### TTFT 差异归因

> [分析为什么 vLLM 的 TTFT 比 Ollama 快/慢：
> - PagedAttention 避免 KV Cache 重复计算
> - Cloud GPU 网络延迟 vs 本地直连
> - 模型加载方式差异
> - 冷启动 vs 预热影响]

---

## 七、遇到的问题与解决

| ID | 优先级 | 问题 | 根因 | 解决 |
|----|--------|------|------|------|
| T-006 | [P0/P1/P2] | [描述] | [根因] | [解决方案] |
| T-007 | [P0/P1/P2] | [描述] | [根因] | [解决方案] |

---

## 八、与 SRE-LAB K3S 方案的对比思考

> [本次云 GPU 压测对 SRE-LAB 项目的启示]

| 维度 | AI Infra Gateway (裸金属) | SRE-LAB (K3S) | vLLM 云实例 |
|------|-------------------------|---------------|-----------|
| 部署复杂度 | 低（单进程） | 中（需 K3S + kubectl） | 低（SSH + 一键脚本） |
| GPU 调度 | Ollama 单实例 | Ollama StatefulSet + HPA | vLLM Continuous Batching |
| 可扩展性 | ❌ 单机 | ✅ HPA 自动扩缩 | ✅ 云 GPU 弹性 |
| 可观测性 | pynvml 仪表盘 | Prometheus + Grafana | [填写] |
| 适用场景 | 开发/调试/边缘 | 生产集群 | 高性能压测/模型验证 |

---

## 九、下一步计划

1. **大模型测试**: 利用 RTX 4090 24GB 测试 qwen2.5:3B 和 7B
2. **GPU 利用率对比**: 采集 nvidia-smi 曲线，对比 vLLM vs Ollama 计算效率
3. **KV Cache 碎片率**: 采集 vLLM 的 block usage 指标
4. **SRE-LAB 对标**: 将 vLLM 部署为 SRE-LAB K3S 集群的一个推理后端
5. **生产化推荐**: 给出 "Ollama / vLLM / SGLang 选型决策树"

---

## 十、数据文件归档

| 文件 | 路径 |
|------|------|
| 原始压测数据 | `05-autodl-benchmark/data/raw_benchmark.json` |
| 汇总数据 | `05-autodl-benchmark/data/benchmark_summary.json` |
| 自动报告 | `05-autodl-benchmark/data/REPORT.md` |
| 对比数据 | `05-autodl-benchmark/data/comparison.json` |

---

*模板基于 SRE-LAB 压测方法论（场景分层 50/30/15/5 + 阶梯加压 1-4-8-16-32）对齐编写。*
*填写完成后，将此文件重命名为 `work_report_YYYYMMDD.md` 并提交。*