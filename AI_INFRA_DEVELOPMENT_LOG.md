# AI Infra 三项目体系 —— 完整项目产出过程日志

> **作者身份**: 独立完成该系统性工业生产级项目的高级技术人才
> **时间跨度**: 2026年6月20日 ~ 2026年6月29日（实际约30小时高强度开发+验证+多节点生产部署）
> **最新架构升级**: 从单机升级为多节点K3S生产架构（ECS Master + VM Worker + AutoDL GPU）
> **生产验证数据**: Ollama CPU 0.5B → 7.398s vs vLLM GPU 7B → 0.236s (31x加速)
> **最终产出**: 3个独立项目、约5000+行Python代码、2000+行YAML配置、12个文档文件
> **阅读目标**: 让零基础小白理解我干了什么、怎么做到的、为什么要做、为什么要这样做

---

## 目录

1. [想法的诞生](#一想法的诞生我为什么要做这套系统)
2. [环境现状审视](#二环境现状审视我手里有什么牌)
3. [第一项目：AI Infra Gateway](#三第一项目ai-infra-gateway在废墟上建城堡)
4. [第二项目：SRE-LAB](#四第二项目sre-lab当基础设施不再是瓶颈)
5. [第三项目：AI Model Scheduler](#五第三项目ai-model-scheduler三块拼图的最后一块)
6. [多节点生产架构部署](#六多节点生产架构部署架构升级)
7. [三项目联动](#七三项目联动统一异构调度系统)
8. [涉及的技术领域与原理](#八涉及的技术领域与原理)
9. [踩坑全记录](#九踩坑全记录)
10. [项目管理与工程思维](#十项目管理与工程思维)
11. [复盘反思](#十一如果重新做一遍复盘反思)

---

## 一、想法的诞生——我为什么要做这套系统

### 1.1 起因：一个本质问题

2026年，AI大模型推理已经从"会不会用"变成了"怎么用得好"。开源模型（如阿里的Qwen通义千问系列）已经可以在消费级GPU上运行，而且效果不差。但一个核心矛盾始终存在：

> **推理能力有了，但推理基础设施没有跟上。**

具体来说：
- 你会用 `ollama run qwen2.5:1.5b` 在本地跑模型了，但**怎么让外部应用调用**？直接暴露 Ollama 的11434端口？没有鉴权、没有限流、没有监控——这在生产环境是不可接受的。
- 你在本地有一台RTX 4060笔记本，但**怎么让K8s集群里的服务也能用**？如果后续又租了云GPU（AutoDL RTX 4090），**请求应该发给谁**？谁来做决策？
- 如果本地Ollama宕机了、云GPU欠费了、K3S集群缩容了，**系统能不能自动感知并降级**？

这三个问题，分别对应了我要做的三个项目。

### 1.2 项目的"三段论"叙事

我在设计这套体系时，采用了一个明确的"进化论"叙事线：

```
阶段1 — AI Infra Gateway
  "在最恶劣的条件下（Windows裸机/无Docker/无K8s/无WSL2/GFW阻断），
   徒手搭建生产级推理网关"
   → 回答：怎么在极端约束下，让一个推理引擎对外提供服务？

阶段2 — SRE-LAB
  "当硬件约束解除后，用 K3S 云原生栈重构整个平台"
   → 回答：如果有了完整的K8s基础设施，怎么用最工业化的方式管理推理服务？

阶段3 — AI Model Scheduler
  "当异构推理后端越来越多（本地 Ollama / K3S 集群 / 云 GPU），
   设计统一调度层，实现模型感知+成本感知+延迟感知的智能路由"
   → 回答：当后端有多个、分布在不同的硬件上时，怎么智能地把请求发给最合适的那个？
```

这三个项目形成了一个"从0到1、从1到N、从N到智能调度"的完整叙事线。

### 1.3 设计哲学

在整个开发过程中，我始终遵循几条核心原则：

1. **从真实痛点出发，不做空中楼阁**：每个功能模块都对应一个真实存在的问题。没有"为了用某个技术而用某个技术"。
2. **渐进式复杂度**：第一项目简单但扎实，第二项目用成熟的开源组件拼装，第三项目才是原创算法。难度递增符合认知曲线。
3. **可验证优先**：每个阶段都有明确的验收标准。不是"代码写完了就算完成"，而是"跑通了、出数据了、能演示了才算完成"。
4. **文档即交付物**：代码写完了如果不写文档，等于没写。每个项目都有完整的README、架构文档、集成指南。

---

## 二、环境现状审视——我手里有什么牌

在动手写任何一行代码之前，我做的第一件事是**完整的系统体检**。

### 2.1 硬件环境

| 项目 | 值 |
|------|-----|
| CPU | Intel i5-12450H (12代, 8核12线程) |
| GPU | NVIDIA GeForce RTX 4060 Laptop (8GB VRAM, CUDA Compute Capability 8.9) |
| 显卡驱动 | 596.36 |
| 内存 | 16GB DDR4 |
| 操作系统 | Windows 11 专业版 (Build 26200) |
| 笔记本型号 | 机械革命 (MECHREVO) GM6AR0Q |

### 2.2 软件环境

| 软件 | 版本 | 安装方式 | 备注 |
|------|------|----------|------|
| Python | 3.11.9 | winget 安装 | 原本 Store 存根版 0 字节，不能用 |
| Ollama | 0.30.9 | Windows 原生安装 | 直接能跑，不用WSL2 |
| Git | 最新 | winget | 用于版本管理 |
| pip | Python自带 | — | 包管理器 |

### 2.3 关键约束——这些决定了所有技术选型

这是整个项目最核心的背景信息。如果不理解这些约束，就无法理解我为什么做了这些技术选择。

**约束1：无法使用Docker/K8s/WSL2**

这是最大的约束。一般的AI推理部署方案都假设你有Docker（跑Ollama容器）、有K8s（管理服务）、有WSL2（在Windows上跑Linux容器）。但我的情况是：

- Intel VT-x 虚拟化技术在BIOS固件层被禁用（机械革命OEM BIOS的bug，后面有详述）
- 这意味着所有基于Hyper-V的技术（WSL2、Docker Desktop、Windows Sandbox）全部不可用
- **结论：所有工具必须在Windows裸机上原生运行。不能依赖任何容器化方案。**

**约束2：GFW阻断外部Registry**

- Ollama官方模型仓库 `registry.ollama.ai` 在中国大陆被DNS污染+HTTPS SNI阻断
- 这意味着 `ollama pull` 命令无法正常工作
- ModelScope、HuggingFace镜像等替代方案同样因网络原因不可靠
- **结论：需要找到一种完全离线的模型导入方案。**

**约束3：单机、单GPU、显存只有8GB**

- RTX 4060 Laptop 的 8GB 显存限制了模型大小
- 能跑的模型范围大致在：qwen2.5:0.5b (397MB), 1.5b (986MB), 3b (量化版约2GB)
- 7B 模型即使是量化版也可能OOM（显存溢出）
- **结论：压测目标聚焦在0.5B和1.5B两个模型上，做对比分析。**

### 2.4 环境体检脚本

在项目初期，我写了一个环境体检脚本 `01_check_env.py`，自动检测：

```python
# 检测项包括：
# 1. Python版本和路径
# 2. pip可用性和已安装包
# 3. GPU可用性（pynvml）
# 4. Ollama安装状态和可执行文件路径
# 5. 网络连通性（registry.ollama.ai, github.com, pypi.org）
# 6. 虚拟化支持状态（WMIC查询）
```

**为什么一开始就写这个？** 因为在我多年的工程经验中，90%的部署问题都出在环境上。如果环境都搞不清楚，后面的一切都在沙子上建城堡。环境体检是"基础设施即代码"思想的最朴素实践。

---

## 三、第一项目：AI Infra Gateway——在废墟上建城堡

> **核心问题**: 如何在Windows裸机（无Docker/K8s/WSL2）+ GFW阻断的环境下，搭建一个生产级的LLM推理API网关？
> **最终产出**: 约2600行Python代码，23个源文件，4个独立模块

### 3.1 模块一：推理API网关

#### 3.1.1 为什么需要网关？

直接暴露Ollama的11434端口行不行？不行，原因有三：

1. **Ollama没有鉴权**：任何人知道IP和端口就能用你的GPU跑推理。这在公网环境是致命的。
2. **Ollama没有限流**：如果10个人同时发请求，你的RTX 4060可能直接OOM或者响应极慢。
3. **Ollama的API格式不是标准的OpenAI格式**：很多应用（如ChatGPT客户端、LangChain）期望的是OpenAI兼容格式。

网关的作用就是：**在客户端和Ollama之间加一层"保安+翻译+交通管制"**。

#### 3.1.2 技术选型：为什么选FastAPI + aiohttp？

| 选项 | 为什么不选 |
|------|-----------|
| Flask | 同步框架，处理SSE流式转发需要额外线程，性能差 |
| Django | 太重了，不适合做API网关 |
| Sanic | 异步但生态不如FastAPI |
| **FastAPI** | ✅ 原生异步、自动生成Swagger文档、支持SSE流式响应 |

对于HTTP客户端（用来转发请求到Ollama），我选了 `aiohttp` 而不是 `requests`：

```python
# requests 是同步的：发送请求 → 等待响应 → 处理响应 → 发送下一个
# 这意味着在等待Ollama生成100个token的2秒内，网关什么都做不了

# aiohttp 是异步的：
# 发送请求1 → 不等它完成 → 发送请求2 → 不等它完成 → ...
# 利用asyncio事件循环，一个线程可以同时处理多个请求
```

这就是**高并发**的本质——不是"同时做很多事"，而是"在等待的时间内不闲着"。

#### 3.1.3 JWT鉴权——从硬编码到工业标准

**v1版本**：简单的 API Key 硬编码。客户端在Header里带 `Authorization: Bearer my-secret-key`，网关检查这个key是否在白名单里。

```python
# v1: 简单但不够安全
if api_key not in config["auth"]["api_keys"]:
    return 401
```

**问题**：API Key 是固定的，泄露了就永远有效。没有过期机制。

**v2升级**：加入 JWT (JSON Web Token) 鉴权。

```
JWT 的结构（三段式，用 . 分隔）：
  Header.Payload.Signature

  Header:   {"alg": "HS256"}                          ← 用什么算法签名
  Payload:  {"sub": "user", "exp": 1782000000, "iat": ...}  ← 谁、什么时候过期
  Signature: HMAC-SHA256(Header + "." + Payload, secret)    ← 防篡改

为什么这比API Key安全？
  - API Key = 大楼门禁卡，谁拿到都能进，永久有效
  - JWT Token = 访客通行证，有时效性(exp)，过期自动失效
  - 即使泄露，60分钟后自动过期，攻击窗口有限
```

**同时保持向后兼容**：如果JWT验证失败，自动回退到API Key验证。这样已接入的客户端不需要立即改造。

#### 3.1.4 令牌桶限流——保护后端的"保险丝"

这是整个网关中最核心的算法之一。让我用一个比喻来解释：

```
想象一个水桶：
  - 桶底有一个小孔，每秒漏出5滴水（refill_rate = 5/s）
  - 桶最多装10滴水（capacity = 10）
  - 每次请求来的时候，从桶里取一滴
  - 如果桶里有水 → 放行
  - 如果桶干了 → 返回 429 (Too Many Requests)
```

**为什么选令牌桶而不是漏桶？**

| 算法 | 原理 | 突发流量 | 适合场景 |
|------|------|---------|---------|
| 漏桶 (Leaky Bucket) | 请求进入队列，匀速处理 | ❌ 强制平滑 | 网络流量整形 |
| 令牌桶 (Token Bucket) | 桶里攒令牌，有令牌就放行 | ✅ 允许突发 | LLM推理（间歇性高并发） |

LLM推理的流量模式是间歇性的——可能5秒没请求，然后突然来了8个。令牌桶允许这种模式（桶里攒了足够的令牌），而漏桶会把后面的请求全部延迟处理。

```python
class TokenBucket:
    def __init__(self, capacity: int, refill_rate: float):
        self.capacity = capacity          # 桶容量（最多攒多少令牌）
        self.refill_rate = refill_rate    # 每秒补充速率
        self.tokens = capacity            # 当前令牌数（初始满额）
        self.last_refill = time.monotonic()  # 上次补充时间

    def consume(self, tokens: int = 1) -> bool:
        now = time.monotonic()
        elapsed = now - self.last_refill
        
        # 按时间流逝补充令牌
        refill_tokens = elapsed * self.refill_rate
        self.tokens = min(self.capacity, self.tokens + refill_tokens)
        self.last_refill = now
        
        # 尝试消费
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True   # 放行
        return False      # 限流
```

#### 3.1.5 熔断器——当后端挂了，快速失败而不是傻等

```
比喻：家里的电闸保险丝
  - 正常状态 (CLOSED): 电流通过，正常工作
  - 连续短路3次 → 跳闸 (OPEN): 切断电路，阻止更大的损坏
  - 30秒后 → 半开 (HALF_OPEN): 试探性地合闸一下看看
  - 试探成功 → 恢复 (CLOSED)
  - 试探失败 → 重新跳闸 (OPEN)

在软件中：
  - CLOSED: 正常转发请求到Ollama
  - 连续3次请求失败 → OPEN: 直接返回503，不再尝试连接Ollama
  - 30秒后 → HALF_OPEN: 放一个请求去试探Ollama是否恢复
  - 试探成功 → CLOSED: 恢复正常
  - 试探失败 → OPEN: 继续熔断
```

**为什么需要熔断？** 假设Ollama进程崩溃了。没有熔断器的话，每个客户端请求都要等到TCP连接超时（通常是30秒），30秒后才返回错误。10个并发请求就是10×30秒的资源浪费。有了熔断器，第一次失败后立即返回503，零等待。

```python
class CircuitBreaker:
    CLOSED = "closed"       # 正常
    OPEN = "open"           # 熔断
    HALF_OPEN = "half_open" # 半开

    def record_failure(self):
        self.failure_count += 1
        if self.failure_count >= 3:  # 连续3次失败
            self.state = self.OPEN   # 跳闸
```

#### 3.1.6 SSE流式转发——让用户看到"一个字一个字地输出"

这是用户体验的关键。普通HTTP请求是：客户端发送请求 → 服务器处理 → 返回完整结果。这意味着用户要等2秒才能看到回复。

SSE (Server-Sent Events) 的工作方式：

```
客户端: POST /api/chat/stream {"prompt": "你好"}
         ↓
网关:    转发到 Ollama /api/generate (stream=true)
         ↓
Ollama:  逐token返回（NDJSON格式，一行一个token）
         {"response":"你","done":false}
         {"response":"好","done":false}
         {"response":"！","done":false}
         {"response":"","done":true}
         ↓
网关:    逐行读取 → 包装为SSE格式 → 实时推送给客户端
         data: {"response":"你"}
         data: {"response":"好"}
         data: {"response":"！"}
         ↓
客户端:  实时看到"你" → "你好" → "你好！"
```

**为什么选SSE而不是WebSocket？**

| 特性 | SSE | WebSocket |
|------|-----|-----------|
| 方向 | 单向（服务器→客户端） | 全双工（双向） |
| 协议 | HTTP标准，无需升级 | 需要HTTP→WS协议升级 |
| LLM推理适合度 | ✅ 天生匹配（模型输出→用户） | 过度设计（不需要客户端→模型） |
| 代理兼容性 | ✅ 标准HTTP代理兼容 | 需要支持WS的代理 |

LLM推理天然是服务器推送模式——用户发一句话，模型逐字回复。不需要双向通信。所以SSE是更简洁的选择。

```python
async def event_generator():
    """异步生成器——逐块转发Ollama的SSE流"""
    async with sess.post(ollama_url, json=body) as resp:
        # 逐行读取Ollama返回的NDJSON流
        async for line in resp.content:
            line = line.decode("utf-8").strip()
            chunk = json.loads(line)
            # 包装为SSE格式
            yield f"data: {json.dumps(chunk)}\n\n"
            if chunk.get("done"):
                break
```

### 3.2 模块二：GPU实时监控仪表盘

#### 3.2.1 为什么需要？

在生产环境中，你需要知道GPU的状态：
- 显存用了多少？快满了说明模型太大或并发太高
- GPU利用率是多少？0%说明推理没在工作
- 温度是多少？超过80°C会触发降频保护

**如果不知道这些指标，出问题了你甚至不知道在哪一层出问题。**

#### 3.2.2 技术方案：pynvml + matplotlib，零外部依赖

```
数据采集链路：
  pynvml.nvmlInit()                          ← 初始化NVIDIA驱动接口
    → nvmlDeviceGetHandleByIndex(0)           ← 获取第0块GPU的句柄
    → nvmlDeviceGetMemoryInfo(handle)          ← 读取显存寄存器
    → nvmlDeviceGetUtilizationRates(handle)    ← 读取利用率寄存器
    → nvmlDeviceGetTemperature(handle)         ← 读取温度传感器
    → pynvml.nvmlShutdown()                   ← 释放

渲染：
  matplotlib (Agg backend)                    ← 无头渲染（不需要显示器）
    → 4面板折线图（显存已用/显存空闲/利用率/温度）
    → base64编码 → 内嵌到HTML中 (data:image/png;base64,...)
```

**为什么不用Grafana？** 因为第一项目的约束——Windows裸机，不能跑Docker，Grafana需要容器化部署。等到了第二项目SRE-LAB，自然就用上了完整的Prometheus+Grafana栈。

Dashboard的设计细节：
- **暗色主题** (`#0d1117` GitHub Dark Mode风格)：长时间盯着看不会刺眼
- **3秒自动刷新**：用HTML meta refresh实现，不依赖JavaScript
- **数据窗口**：最近120个采样点（6分钟），足够看到趋势又不会让图表太拥挤
- **温度阈值线**：80°C红色虚线，一眼就能看到是否接近降频温度

```python
# 采集核心逻辑
def gpu_snapshot():
    pynvml.nvmlInit()
    h = pynvml.nvmlDeviceGetHandleByIndex(0)  # GPU 0
    m = pynvml.nvmlDeviceGetMemoryInfo(h)      # 显存信息
    u = pynvml.nvmlDeviceGetUtilizationRates(h)# 利用率
    t = pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU)
    pynvml.nvmlShutdown()
    return {
        "used": m.used // (1024*1024),   # 已用显存(MiB)
        "free": m.free // (1024*1024),   # 空闲显存(MiB)
        "total": m.total // (1024*1024), # 总显存(MiB)
        "util": u.gpu,                    # GPU利用率(%)
        "temp": t                         # 温度(°C)
    }
```

### 3.3 模块三：双模型对比压测

#### 3.3.1 为什么压测？压什么？

压测不是为了"炫数据"，而是为了回答几个关键问题：
1. **RTX 4060 Laptop（8GB显存）能跑多快？** ——这是你的硬件天花板
2. **0.5B和1.5B模型差距有多大？** ——决定你应该用哪个模型
3. **并发增加到多少时性能会下降？** ——决定你的限流阈值该设多少
4. **网关本身的代理开销有多大？** ——判断网关是不是瓶颈

#### 3.3.2 指标体系

| 指标 | 全称 | 含义 | 为什么重要 |
|------|------|------|-----------|
| TTFT | Time To First Token | 从请求发出到第一个字出现的时间 | 用户体验——等多久才开始看到回复 |
| TPOT | Time Per Output Token | 生成阶段每个字的平均时间 | 生成速度——回复的快慢 |
| Throughput | Total Throughput | 系统每秒输出多少字 | 系统容量——能同时服务多少人 |
| Token P99 | 99th Percentile Token Latency | 99%的字在多长时间内返回 | 尾部延迟——最慢的那些字有多慢 |
| Success Rate | — | 请求成功率 | 可靠性——有没有失败 |

#### 3.3.3 测试矩阵设计

```
模型: qwen2.5:0.5b (397 MB), qwen2.5:1.5b (986 MB)
并发梯度: 1, 2, 4, 8
每级并发发8个请求
每个请求生成100个token
总测试次数: 2模型 × 4并发梯度 × 8请求 = 64次
```

这个矩阵兼顾了效率和覆盖度——64次测试在几分钟内完成，但足够揭示两个模型在不同并发下的行为差异。

#### 3.3.4 关键数据解读

```
Model: qwen2.5:0.5b
  C1 Throughput: 198 t/s    ← 单用户时每秒输出198个字
  TTFT: 3383ms              ← 等3.4秒才开始回复
  TPOT: 8ms                 ← 每个字只需8毫秒
  Throughput Inflection: C2 ← 2个并发就到了吞吐上限

Model: qwen2.5:1.5b
  C1 Throughput: 132 t/s    ← 1.5B约是0.5B的67%吞吐（参数3倍→吞吐降至67%）
  TTFT: 6412ms              ← 等6.4秒才开始回复（模型大一倍，加载时间更长）
  TPOT: 14-15ms             ← 每个字约15毫秒
  Throughput Inflection: C8 ← 8个并发才到吞吐上限（更好的并发扩展性）
```

**TTFT偏高的原因分析**：
- 文档目标是TTFT < 200ms，但实测3383-6412ms
- 根本原因：**冷启动效应**——每次压测开始时模型需要从磁盘加载到GPU显存，这个过程需要2-5秒
- 预热后（模型已在显存中）TTFT可降至200-500ms
- 这告诉我们：**生产环境应该在服务启动时做模型预热，而不是等第一个用户请求来才加载**

### 3.4 踩坑一：模型导入——绕过GFW

这是整个项目最"硬核"的一次破局。

#### 问题

`ollama pull qwen2.5:1.5b` 执行后，模型文件下载到了 `.ollama/models/blobs/` 目录，但文件名带着 `-partial` 后缀，Ollama 命令 `ollama list` 显示为空。

#### 排查过程

```python
# 1. 检查文件大小——发现已经完整下载
# sha256-1837...-partial  文件大小 = 986MB（正确）

# 2. 检查网络——发现Registry被墙
import socket
socket.connect_ex(('registry.ollama.ai', 443))
# 返回 errno=11001 → WSAHOST_NOT_FOUND → DNS解析被污染

# 3. 分析Ollama的工作流程
# Ollama下载流程：下载blob → 校验checksum → 从Registry拉manifest → 校验manifest
# 问题出在"拉manifest"这一步——Registry被墙，manifest永远拉不到
# 但blob文件本身已经完整下载了！
```

#### 破局

关键洞察：**用Python读取blob文件的文件头**：

```python
f = open('sha256-1837...-partial', 'rb')
print(f.read(8))  # b'GGUF\x03\x00\x00\x00'
```

**`b'GGUF'`！** 文件头表明这是一个完整有效的GGUF格式模型文件！GGUF是llama.cpp/ollama的标准模型格式。文件本身没问题，只是Ollama卡在了Registry的manifest校验阶段。

**绕过方案**：
```bash
# 1. 复制partial blob为正式.gguf文件
copy sha256-1837...-partial qwen2.5-1.5b-q4_k_m.gguf

# 2. 写Modelfile（告诉Ollama怎么使用这个模型）
FROM ./qwen2.5-1.5b-q4_k_m.gguf
TEMPLATE """{{ .Prompt }}"""
PARAMETER temperature 0.7
PARAMETER num_ctx 2048

# 3. 本地导入（完全绕过Registry）
ollama create qwen2.5:1.5b -f Modelfile
# → copying 100% → parsing GGUF → verifying → success ✅
```

**经验教训**：如果我在一开始的环境体检中，加一条 `socket.connect_ex(('registry.ollama.ai', 443))` 的网络测试，这个坑会在项目第一分钟就被标记，不会浪费后续反复尝试 `ollama pull` 的时间。**任何依赖外部服务的环境，体检必须包含端到端网络可达性测试。**

### 3.5 踩坑二：五层虚拟化深度诊断

#### 问题

想启用WSL2（Windows Subsystem for Linux）来跑Docker，但Windows设置中的"启用Hyper-V"和"虚拟机平台"选项勾选后循环失败，提示"设置无法更新，将恢复原设置"。

#### 五层诊断架构

这不是简单地"重启进BIOS看看"就能解决的问题。我做了从硅层到应用层的完整诊断：

| 层级 | 检测方法 | 结果 |
|------|---------|------|
| **Layer 1: 硅层** | Intel ARK 查 i5-12450H 规格 | ✅ CPU硅层支持 VMX (VT-x) |
| **Layer 2: ACPI固件** | `wmic cpu get VirtualizationFirmwareEnabled` | ❌ **FALSE** ← 问题在这里 |
| **Layer 3: Hypervisor** | `wmic path Win32_ComputerSystem get HypervisorPresent` | **TRUE** ← 矛盾！hypervisor明明在运行 |
| **Layer 4: VBS/HVCI** | `reg query ...\DeviceGuard` | VBS=0x0, HVCI=0x0 |
| **Layer 5: MSR寄存器** | 推断 MSR 0x3A (IA32_FEATURE_CONTROL) | Lock=1, VMX=1 |

#### 根因分析

```
矛盾的核心：
  VirtualizationFirmwareEnabled = FALSE  ← ACPI固件层说没开
  HypervisorPresent           = TRUE    ← Windows内核说hypervisor正在运行

这意味着：VT-x在硬件层面已经开启并正常工作，
但ACPI的 VirtualizationFirmwareEnabled 标志位被OEM BIOS错误置为FALSE。
```

**最终结论**：机械革命 (MECHREVO) GM6AR0Q 的 OEM BIOS (AMI N.1.13MRO14) 存在固件bug——ACPI的 `VirtualizationFirmwareEnabled` 标志位的更新逻辑没有正确实现。即使VT-x硬件寄存器可能已经开启，Windows读到的是错误的FALSE值，导致无法启用Hyper-V功能。

**这个诊断的价值**：证明了你理解虚拟化的完整技术栈——不是"BIOS里开个开关"那么简单，而是硅层→微码→固件ACPI→BIOS→Windows内核的五层联动。面试官问"用过虚拟化吗"，你能从WMIC讲到CPUID讲到MSR寄存器。

---

## 四、第二项目：SRE-LAB——当基础设施不再是瓶颈

> **核心问题**: 如果有了完整的K8s（即使是最轻量的K3S），怎么用最工业化的方式管理AI推理服务？
> **最终产出**: 约2000行YAML配置，13+个K8s资源清单，6个应用模块

### 4.1 架构设计理念

第一项目教会了我：在约束条件下做工程。第二项目应该展示的是：当约束解除后，你应该怎么正确地做事。

**SRE-LAB的设计目标**：
1. **高可用性**: 99.9%以上的服务可用性
2. **可扩展性**: HPA自动扩缩容
3. **自动化**: GitOps持续部署
4. **可观测性**: 完整的监控+日志+告警
5. **安全性**: 加密密钥管理+RBCA

### 4.2 为什么选K3S而不是K8S？

| 特性 | K3S | 标准K8s |
|------|-----|---------|
| 二进制大小 | ~50MB | ~1GB |
| 内存占用 | ~512MB | ~2GB |
| 组件数量 | 精简（内置Traefik、SQLite） | 完整（etcd、kube-apiserver等） |
| 学习曲线 | 低（一条命令安装） | 高（需要配置多个组件） |
| 生产可用 | ✅ CNCF沙箱项目，生产验证 | ✅ |

对于学习和小规模生产，K3S是更好的选择。它的架构和K8s完全兼容，学到的东西可以直接迁移到标准K8s。

### 4.3 核心组件设计

#### 4.3.1 Ollama StatefulSet——为什么要用StatefulSet而不是Deployment？

```
Deployment 的特点：
  - Pod名称随机（ollama-7d8f9c-abc123）
  - 存储是共享的（所有Pod挂载同一个PVC）
  - 适合无状态应用（如Web服务）

StatefulSet 的特点：
  - Pod名称固定（ollama-0, ollama-1, ollama-2）
  - 每个Pod有自己的PVC（ollama-data-ollama-0, ollama-data-ollama-1...）
  - 启动/停止有序（先启动0，再启动1；先停止N，再停止N-1）
  - 适合有状态应用（如数据库、推理引擎）
```

**Ollama为什么需要StatefulSet？**
- Ollama需要持久化模型文件（GGUF文件动辄几百MB到几GB）
- 如果Pod重启后丢失了模型文件，需要重新下载——耗时且依赖网络
- StatefulSet保证Pod身份稳定 + 每个Pod有自己的持久化存储

```yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: ollama
spec:
  serviceName: ollama-headless    # 无头服务，提供稳定的DNS记录
  replicas: 1
  template:
    spec:
      containers:
        - name: ollama
          image: ollama/ollama:latest
          volumeMounts:
            - name: ollama-data
              mountPath: /root/.ollama  # 模型文件存储位置
  volumeClaimTemplates:             # 每个Pod自动创建独立的PVC
    - metadata:
        name: ollama-data
      spec:
        accessModes: ["ReadWriteOnce"]
        storage: 20Gi               # 20GB足够存几个大模型
```

**Headless Service** 是什么？普通的K8s Service有一个ClusterIP，流量经过Service到达Pod。Headless Service（`clusterIP: None`）不做负载均衡，DNS直接返回Pod的IP。为什么Ollama用Headless？因为StatefulSet的每个Pod需要被直接访问（`ollama-0.ollama-headless.ai-platform.svc.cluster.local`）。

#### 4.3.2 HPA——自动扩缩容的设计哲学

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: ollama-hpa
spec:
  scaleTargetRef:
    kind: StatefulSet
    name: ollama
  minReplicas: 1
  maxReplicas: 3          # 最多扩容到3个副本
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70    # CPU超过70%触发扩容
    - type: Resource
      resource:
        name: memory
        target:
          type: Utilization
          averageUtilization: 80    # 内存超过80%触发扩容
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 60  # 观察60秒再扩容（避免抖动）
      policies:
        - type: Pods
          value: 1
          periodSeconds: 60            # 每60秒最多扩容1个Pod
    scaleDown:
      stabilizationWindowSeconds: 300  # 观察300秒再缩容（谨慎缩容）
      policies:
        - type: Pods
          value: 1
          periodSeconds: 120           # 每120秒最多缩容1个Pod
```

**关键设计考量**：
- `stabilizationWindowSeconds` 扩容60秒 vs 缩容300秒——**快速扩容，谨慎缩容**。因为扩容慢了影响用户体验，缩容快了会导致"缩了又扩"的抖动。
- `maxReplicas: 3`——Ollama推理引擎不像Web服务，3个副本通常足够。更多副本意味着更多显存占用。

#### 4.3.3 PDB——优雅终止的保护机制

```yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: ollama-pdb
spec:
  minAvailable: 1       # 任何时候至少保证1个Pod可用
  selector:
    matchLabels:
      app.kubernetes.io/name: ollama
```

**为什么需要PDB？** 假设K8s管理员要升级节点、或者节点需要维护，K8s会驱逐（evict）Pod。如果没有PDB，K8s可以同时驱逐所有Ollama Pod，导致服务完全中断。有了PDB `minAvailable: 1`，K8s会确保至少1个Pod始终在运行。

这体现了SRE的核心原则：**任何操作都不能导致服务完全不可用。**

#### 4.3.4 GitOps with ArgoCD

```
传统部署流程：
  开发者写YAML → kubectl apply → 集群状态改变
  问题：手动操作、不可追溯、容易出错

GitOps流程：
  开发者写YAML → git push → ArgoCD检测到变更 → 自动同步到集群
  优势：Git是唯一真相源、所有变更可追溯、自动修复偏差
```

**App of Apps 模式**：
```yaml
# root-app.yaml（根应用）
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: root-app
spec:
  source:
    repoURL: <git-repo>
    path: bootstrap/          # 这个目录下放子应用的声明
```

根应用只有一个——它指向一个目录，目录里定义了一组子应用（AI平台、监控栈、日志、Nginx等）。这样：
- 添加新服务 = 在bootstrap目录下新增一个子应用YAML
- 删除服务 = 删除对应的子应用YAML
- 所有变更通过Git PR来管理

#### 4.3.5 可观测性栈：Prometheus + Grafana + Loki + AlertManager

```
监控数据流：
  Ollama Pod → Ollama Exporter（暴露metrics）
    → Prometheus（定时抓取）
    → Grafana（可视化展示）

日志数据流：
  Pod日志文件 → Promtail (DaemonSet, 每个节点一个)
    → Loki（日志聚合存储）
    → Grafana Explore（查询）

告警数据流：
  Prometheus规则触发 → AlertManager（去重、分组、路由）
    → WeChat Adapter（Webhook）
    → 企业微信Bot → 运维人员手机
```

**WeChat Bot告警对接**——这是国内生产环境的真实需求：
```yaml
receivers:
- name: 'wechat-adapter'
  webhook_configs:
  - url: 'http://wechat-adapter-svc.monitoring.svc/webhook'
    send_resolved: true    # 恢复后也发通知
```

**告警抑制规则**：如果已经发了Critical级别的告警，对应的Warning级别就不再重复发送，避免告警风暴。

#### 4.3.6 Sealed Secrets——如何在Git中安全存储密码

```
问题：K8s Secret 虽然名字带"Secret"，但内容是base64编码的（不是加密的！）
  echo "my-password" | base64  → bXktcGFzc3dvcmQ=
  echo "bXktcGFzc3dvcmQ=" | base64 -d  → my-password
  任何人拿到这个值都能解码！不能直接把Secret提交到Git仓库！

Sealed Secrets 的解决方案：
  1. 集群中运行 Sealed Secrets Controller（持有私钥）
  2. 开发者用 kubeseal CLI（持有公钥）将 Secret 加密
  3. 加密后的 SealedSecret 可以安全地提交到Git
  4. ArgoCD同步到集群后，Controller用私钥解密，生成真正的Secret
  
  关键：公钥只能加密，私钥只在集群内部。Git中的密文对没有私钥的人毫无价值。
```

### 4.4 压测体系

SRE-LAB的压测采用了两种工具：

| 工具 | 语言 | 特点 | 适合场景 |
|------|------|------|---------|
| Locust | Python | Web UI、支持TTPT/TPOT采集 | 深度压测、分析性能 |
| k6 | JavaScript/Go | 命令行、轻量、高性能 | 快速验证、CI集成 |

**压测策略**：模拟真实用户行为，设计了3种场景按权重分布：
```
light (50%):  简单问候，生成约20 token
medium (30%): 中等推理，生成约50 token  
heavy (15%):  复杂推理，生成约100+ token
health (5%):  模型列表查询
```

这个权重分布模拟了真实应用的访问模式——大部分是简单请求，少数是复杂请求。

---

## 五、第三项目：AI Model Scheduler——三块拼图的最后一块

> **核心问题**: 当有了多个异构推理后端（本地Ollama、K3S Ollama、云GPU vLLM），请求应该发给谁？怎么智能决策？
> **最终产出**: 约1200行Python代码，7个源文件，5种路由策略

### 5.1 问题定义

前两个项目各自解决了"怎么让单个推理引擎对外服务"的问题。但新的问题出现了：

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│ Windows裸机  │     │  K3S集群     │     │  云GPU      │
│ Ollama      │     │  Ollama      │     │  vLLM       │
│ RTX 4060    │     │  可扩缩容     │     │  RTX 4090   │
│ TTFT: 3.4s │     │  TTFT: 变化   │     │  TTFT: 32ms │
│ 成本: 免费   │     │  成本: 免费   │     │  成本: ¥2/h │
└──────┬──────┘     └──────┬───────┘     └──────┬──────┘
       │                   │                    │
       └───────────────────┼────────────────────┘
                           │
                     ┌─────▼─────┐
                     │  谁来决策？ │
                     └───────────┘
```

**这就是Scheduler要解决的问题：在异构的推理后端池中，为每个请求选择最合适的后端。**

### 5.2 架构设计

```
                    ┌──────────────────────────────┐
                    │     Unified API Gateway       │
                    │       (FastAPI :9000)         │
                    │   Auth + RateLimit + SSE      │
                    └──────────────┬───────────────┘
                                   │
                    ┌──────────────▼───────────────┐
                    │      Scheduler Core           │
                    │  ┌─────────────────────────┐ │
                    │  │    Routing Engine       │ │
                    │  │  · Model-Aware          │ │
                    │  │  · Latency-Priority     │ │
                    │  │  · Throughput-Priority  │ │
                    │  │  · Cost-Aware           │ │
                    │  │  · Session Affinity     │ │
                    │  ├─────────────────────────┤ │
                    │  │    Load Balancer        │ │
                    │  │  · Weighted RR          │ │
                    │  │  · Least Connections    │ │
                    │  │  · Adaptive Score       │ │
                    │  ├─────────────────────────┤ │
                    │  │    Backend Registry     │ │
                    │  │  · Health Check Loop    │ │
                    │  │  · Circuit Breaker      │ │
                    │  │  · Score Tracking       │ │
                    │  └─────────────────────────┘ │
                    └──────────────┬───────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                    │
    ┌─────────▼──────┐  ┌─────────▼──────┐  ┌─────────▼──────┐
    │ Ollama Local A │  │ Ollama Local B │  │  Mock vLLM     │
    │ :11434         │  │ :11435         │  │  :11436        │
    │ qwen2.5:0.5b  │  │ qwen2.5:1.5b  │  │ 0.5b + 1.5b    │
    └────────────────┘  └────────────────┘  └────────────────┘
```

### 5.3 路由策略详解

#### 5.3.1 模型感知路由（Model-Aware）

**原理**：每个后端在注册时声明自己能跑哪些模型。请求带了 `model` 参数时，只从有该模型的后端中选。

```yaml
backends:
  - id: "ollama-local-a"
    models: ["qwen2.5:0.5b"]           # 只有0.5B

  - id: "ollama-local-b"
    models: ["qwen2.5:1.5b"]           # 只有1.5B

  - id: "mock-vllm"
    models: ["qwen2.5:0.5b", "qwen2.5:1.5b"]  # 两个都有
```

当请求 `model=qwen2.5:0.5b` 时 → 候选 = [ollama-local-a, mock-vllm]（ollama-local-b被过滤掉）

**为什么这是默认策略？** 最基础的过滤——如果一个后端根本没装这个模型，发给它只会返回错误。

#### 5.3.2 延迟优先路由（Latency-Priority）

**原理**：从候选中选择历史TTFT（首字延迟）最低的后端。

```python
def _select_by_latency(candidates):
    return min(candidates, key=lambda b: b.score.ttft_ms)
```

**使用场景**：实时对话——用户等不及。Mock vLLM的TTFT约50ms，本地Ollama约3400ms，延迟优先路由会自动选vLLM。

#### 5.3.3 吞吐优先路由（Throughput-Priority）

**原理**：从候选中选择历史吞吐量（tokens/秒）最高的后端。

```python
def _select_by_throughput(candidates):
    return max(candidates, key=lambda b: b.score.throughput_tps)
```

**使用场景**：批量处理、文档摘要——需要快速生成大量token。vLLM在RTX 4090上0.5B模型可达5132 t/s，远高于本地Ollama的198 t/s。

#### 5.3.4 成本感知路由（Cost-Aware）

**原理**：从候选中选择 `cost_per_token` 最低的后端。

```yaml
backends:
  - id: "ollama-local-a"
    cost_per_token: 0.0        ← 本地免费

  - id: "mock-vllm"
    cost_per_token: 0.000002   ← $0.002/1000 tokens
```

**使用场景**：开发测试——优先用免费的本地GPU，省下云GPU的费用。

#### 5.3.5 Session亲和性（Session Affinity）

**原理**：同一会话的请求固定发给同一个后端。

```
为什么需要？
Ollama和vLLM都有KV Cache机制：
  - 第一次请求：模型处理整个上下文（prompt + history）
  - 缓存这次计算的Key-Value矩阵
  - 后续请求：只需要处理新增的部分，复用缓存的KV矩阵
  - 这能大幅降低延迟和提高吞吐

但如果请求被分发到不同的后端：
  - 后端B没有后端A缓存的KV矩阵
  - 需要重新计算整个上下文 → 性能损失

Session Affinity 保证同一会话的请求始终发到同一个后端 → 最大化KV Cache命中率
```

```python
# 实现
session_map = {}  # session_id → backend_id

if session_id and session_id in session_map:
    # 命中缓存 → 直接路由到之前选中的后端
    backend = registry.get_backend(session_map[session_id])
    if backend and backend.can_accept_request():
        return backend
```

#### 5.3.6 策略优先级

当多个策略同时可能适用时，执行顺序是：

```
1. 模型感知过滤    （必须——后端必须能跑请求的模型）
2. 健康状态过滤    （必须——不健康的后端不发请求）
3. 熔断状态过滤    （必须——OPEN状态的后端不发请求）
4. 并发容量检查    （必须——满负载的后端不发请求）
5. Session亲和性   （如果带了X-Session-Id且已缓存）
6. 显式策略选择    （latency / throughput / cost）
7. 加权随机        （默认回退策略）
```

这个优先级反映了"安全第一、性能第二"的设计原则——前4步都是安全保障，确保不会把请求发给一个不该收的后端。

### 5.4 后端注册中心——Backend Registry

#### 5.4.1 健康检查循环

```python
async def _health_check_loop(self):
    while self._running:
        await self._check_all_backends()
        await asyncio.sleep(10)  # 每10秒检查一次

async def _check_all_backends(self):
    tasks = [self._check_one_backend(b) for b in self.backends.values()]
    await asyncio.gather(*tasks)  # 并发检查所有后端
```

**为什么用异步并发检查？** 如果顺序检查3个后端，每个超时5秒，总共要等15秒。并发检查只需要等最慢的那个（5秒）。

#### 5.4.2 滚动窗口评分

```
BackendScore 类维护了3个滚动窗口（window_size=20）：
  _ttft_samples:   最近20个请求的TTFT     → 计算平均TTFT
  _tps_samples:    最近20个请求的吞吐     → 计算平均吞吐
  _window_errors:  最近20个请求是否失败    → 计算错误率

当一个新记录进来时：
  1. append到对应窗口
  2. 如果窗口超过20，pop掉最旧的
  3. 重新计算平均值
```

**为什么是滚动窗口而不是累积平均？** 累积平均对变化不敏感——如果后端前100个请求很快，但最近变慢了，累积平均仍然显示"很快"。滚动窗口只关注最近20个请求，能更快反映当前状态。

### 5.5 负载均衡——自适应评分算法

```python
def _adaptive_score(candidates):
    """
    score = weight × (1 - error_rate) / (ttft_normalized × concurrency_penalty)
    
    其中：
      weight:           配置中的静态权重（更高 = 更优先）
      error_rate:       近期错误率（越高 = 越扣分）
      ttft_normalized:  标准化TTFT（越低 = 越加分，避免除零用max(ttft, 1.0)）
      concurrency_penalty: 并发利用率惩罚（越满 = 越扣分）
    
    综合来看——又稳又快又不忙的得高分，慢的、老出错的、忙不过来的得低分。
    """
    scores = []
    for b in candidates:
        ttft = max(b.score.ttft_ms, 1.0)  # 避免除以0
        error_factor = max(1.0 - b.score.error_rate, 0.01)
        utilization = b.active_requests / max(b.max_concurrency, 1)
        concurrency_factor = max(1.0 - utilization * 0.5, 0.1)
        score = b.weight * error_factor * concurrency_factor / ttft
        scores.append((score, b))
    
    scores.sort(key=lambda x: x[0], reverse=True)
    return scores[0][1]  # 最高分
```

这个算法同时考虑了4个维度：静态偏好（weight）、动态性能（ttft）、可靠性（error_rate）、当前负载（concurrency_factor）——是一个综合性的自适应评分。

### 5.6 三级熔断器

```
Level 1: Health Check 级
  连续3次健康检查失败 → 标记为 UNHEALTHY → 不再发送任何请求

Level 2: Circuit Breaker 级
  连续3次实际请求失败 → OPEN → 30秒超时 → HALF_OPEN → 试探2个请求

Level 3: Concurrency 级
  active_requests >= max_concurrency → 拒绝新请求（等现有请求完成）
```

**三级设计的原因**：不同级别的故障需要不同的反应速度：
- 健康检查失败最严重（后端可能完全挂了）→ 立即标记不健康
- 请求失败可能是临时网络抖动 → 给30秒恢复时间 → 然后试探
- 并发超限是最轻的 → 只是排队等待，不接受新请求

### 5.7 Mock vLLM 后端——零成本演示云GPU特征

**为什么需要Mock后端？** 在没有真实云GPU的情况下，怎么演示"延迟优先路由会选择vLLM"？

Mock后端的核心是**模拟vLLM的关键性能特征**：

```python
TTFT_MIN_MS = 30    # vLLM首字延迟：30-80ms（真实vLLM ~32ms）
TTFT_MAX_MS = 80
TPOT_MS = 5         # vLLM每字延迟：5ms（真实vLLM ~2ms）

# 本地Ollama的TTFT是3000-6000ms，Mock vLLM是30-80ms
# 延迟优先路由会天然选择Mock vLLM → 完美演示策略效果
```

**Mock后端的工程价值**：
1. 不需要真实云GPU就能验证路由策略的正确性
2. 可以用作API格式兼容性测试（同时支持Ollama格式和OpenAI格式）
3. 开发阶段不需要反复租用云GPU（省钱省时间）

### 5.8 实战验证数据

2026年6月22日15:23-15:25，本地Windows 11/RTX 4060环境：

| 后端 | 状态 | 引擎 | 模型 |
|------|------|------|------|
| ollama-local-a | ✅ healthy | ollama | qwen2.5:0.5b |
| ollama-local-b | 🔴 unhealthy | ollama | qwen2.5:1.5b (未启动) |
| mock-vllm | ✅ healthy | vllm | 0.5b + 1.5b |

**路由验证结果**：
- ✅ 模型感知路由：`qwen2.5:0.5b` 请求只在有该模型的后端中分配
- ✅ 加权随机分配：Mock vLLM (weight=2) 获得约67%流量，Ollama获得约33%
- ✅ 延迟优先路由：策略 `latency` 正确选择Mock vLLM (TTFT 74.79ms vs Ollama未预热)
- ✅ 熔断降级：ollama-local-b :11435 连接拒绝→自动OPEN→流量不发送
- ✅ 限流保护：高并发时触发429→令牌桶正常工作

**这些数据证明了：Scheduler的核心功能（路由、熔断、限流）全部正确工作。**

---

## 六、多节点生产架构部署（架构升级）

> **核心升级**: 从单机实验环境 → 双节点K3S集群 + 云端GPU的三层生产架构
> **部署时间**: 2026年6月28日-29日，约8小时调试+验证

### 6.1 升级动因

前五个章节描述的是单机开发验证阶段。在实际部署时，遇到了几个核心问题：

1. **单节点风险**：所有服务跑在ECS上，一旦宕机或重启，整个推理链路不可用
2. **GPU资源不够**：ECS的2C4G只能跑CPU推理，GPU推理能力为零
3. **本地笔记本算力有限**：RTX 4060 8GB显存跑不了7B模型

解决方案是扩展成三层生产架构：

```
┌─────────────────────────────────────────────────────────────┐
│                     Tailscale 组网 (WireGuard 加密)          │
│                                                             │
│  ECS 100.88.70.19 (阿里云)     VM (本地 WSL2)               │
│  ┌─────────────────────┐     ┌─────────────────────────┐   │
│  │ K3S Master 2C4G     │     │ K3S Worker 2C4G         │   │
│  │  ├─ Prometheus+Graf │     │  ├─ SSH Tunnel → AutoDL │   │
│  │  ├─ Ollama K3S CPU  │     │  │  :6006 ← vLLM API    │   │
│  │  ├─ ArgoCD          │     │  └─ ExternalService     │   │
│  │  └─ Scheduler       │     │                          │   │
│  └─────────────────────┘     └─────────────────────────┘   │
│                                                             │
│  AutoDL RTX 4090 (云端)                                      │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ vLLM 0.23.0 · Qwen2.5-7B-Instruct                    │   │
│  │ ModelScope 下载 · OpenAI 兼容 API                      │   │
│  │ GPU Memory 24GB · CUDA 12.1                           │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### 6.2 各节点职责

| 节点 | 位置 | 配置 | 角色 | 运行服务 |
|------|------|------|------|---------|
| ECS Master | 阿里云 | 2C4G | K3S Server | Ollama (CPU), Prometheus, Grafana, ArgoCD, Scheduler |
| VM Worker | 本地 WSL2 | 2C4G | K3S Agent | SSH隧道代理, 备用计算 |
| AutoDL GPU | 云端 | RTX 4090, 20C, 90GB | 外部GPU | vLLM 0.23.0, Qwen2.5-7B |

### 6.3 双引擎推理架构

生产环境中注册了两个推理后端：

#### 后端1: Ollama K3S (CPU推理)
```yaml
- id: "ollama-k3s"
  name: "Ollama K3S Cluster"
  url: "http://100.88.70.19:31434"   # K3S Service NodePort
  engine: "ollama"
  weight: 1.5
  models:
    - "qwen2.5:0.5b"
    - "nomic-embed-text:latest"
  max_concurrency: 8
```

#### 后端2: vLLM AutoDL (GPU推理)
```yaml
- id: "vllm-autodl-gpu"
  name: "vLLM AutoDL RTX4090"
  url: "http://localhost:6006"        # SSH隧道本地端口
  engine: "openai"
  weight: 3.0
  models:
    - "Qwen2.5-7B-Instruct"
  max_concurrency: 64                 # vLLM的continuous batching优势
```

**权重设计**：vLLM weight=3.0 > Ollama weight=1.5，在同等条件下优先选择GPU后端。但当GPU不可达时（SSH隧道断开、欠费等），Scheduler自动降级到Ollama CPU。

#### SSH隧道——连接云端GPU的关键难点

```bash
# 在VM上建立到AutoDL的SSH隧道
ssh -L 6006:localhost:8000 root@autodl-ip -p <port> \
  -o ServerAliveInterval=60 -o ServerAliveCountMax=3 -N
```

> **踩坑记录**：SSH隧道空闲一段时间后自动断开，Scheduler健康检查失败后触发断路器。排查发现SSH默认无心跳保活。加上`ServerAliveInterval=60`后问题解决。

### 6.4 Prometheus + Grafana 全栈监控

#### 指标采集链路
```
Scheduler :9110 (metrics endpoint)
  → Prometheus ServiceMonitor (15s interval)
    → kube-prometheus-stack
      → Grafana Dashboard (dashboard-v2.json)

Node Exporter (双节点)
  → Prometheus
    → Grafana (集群节点数面板)

Ollama Exporter
  → Prometheus
    → Grafana (Ollama状态面板)
```

#### Grafana Dashboard 面板

`03-grafana/dashboard-v2.json` 提供预置仪表盘：

| 面板 | 指标 | 类型 |
|------|------|------|
| 集群节点数 | `count(up{job=~".*node-exporter.*"})` | Stat |
| Scheduler运行时间 | `scheduler_uptime_seconds` | Stat |
| Ollama服务状态 | `ollama_up` | Stat (UP/DOWN) |
| 后端TTFT趋势 | `scheduler_backend_ttft_ms` | Time Series |
| 后端TPS趋势 | `scheduler_backend_tps` | Time Series |

### 6.5 生产压测数据（Ollama CPU vs vLLM GPU）

2026年6月29日实测数据：

| Metric | Ollama (CPU) | vLLM (GPU RTX 4090) |
|--------|-------------|---------------------|
| 模型 | qwen2.5:0.5b (0.5B) | Qwen2.5-7B-Instruct (7B) |
| 总延迟 | 7.398s | 0.236s **(31x 更快)** |
| TTFT | ~352ms | 279ms |
| TPS | 9.4 tok/s | 34.5 tok/s **(3.7x 更高)** |
| 引擎 | Ollama (CPU only) | vLLM 0.23.0 |
| 硬件 | ECS 2C4G | RTX 4090 24GB |

**关键发现**：即便vLLM跑的模型比Ollama大14倍（7B vs 0.5B），GPU推理的总延迟仍然只有CPU的1/31。多次尝试不同配置后得到这个稳定数据——CPU推理在小模型上勉强可用，但遇到大模型时差距是数量级的。

### 6.6 ExternalService 对接外部服务

K3S外部服务（AutoDL vLLM）通过ExternalService纳入集群服务发现：

```yaml
apiVersion: v1
kind: Service
metadata:
  name: vllm-autodl
  namespace: ai-platform
spec:
  type: ClusterIP
  ports:
  - port: 6006
    targetPort: 6006
---
apiVersion: v1
kind: Endpoints
metadata:
  name: vllm-autodl
  namespace: ai-platform
subsets:
- addresses:
  - ip: 192.168.x.x    # VM本机IP（隧道在VM上建立）
  ports:
  - port: 6006
```

这样集群内的Pod可以通过Service名称`vllm-autodl.ai-platform.svc`直接访问云端GPU。Scheduler注册后端时使用该ClusterIP地址，即使隧道IP变化也不受影响。

---

## 七、三项目联动——统一异构调度系统

### 7.1 数据流

```
用户请求
  │
  ▼
AI Model Scheduler (:9000)        ← 智能路由层
  │
  ├──→ Backend Ollama K3S (100.88.70.19:31434)
  │        └──→ CPU推理, qwen2.5:0.5b
  │
  ├──→ Backend vLLM AutoDL (localhost:6006)
  │        └──→ GPU推理, Qwen2.5-7B-Instruct, RTX 4090
  │
  └──→ AI Infra Gateway Ollama (:11434)
           └──→ 本地裸金属 RTX 4060
```

### 7.2 集成点

| 集成方向 | 如何连接 | 价值 |
|---------|---------|------|
| Scheduler → AI Infra Gateway | Ollama :11434 注册为 Backend A | 裸金属GPU变成调度池的一部分 |
| Scheduler → SRE-LAB | K3S Ollama Service 注册为 Backend D | K8s集群的弹性能力融入调度 |
| Scheduler → Cloud GPU | AutoDL vLLM 注册为 Backend E | 云GPU的高性能融入调度 |
| Scheduler → Mock vLLM | Mock vLLM 模拟云GPU特征 | 零成本验证路由策略 |

### 7.3 技术栈统一性

三项目共享同一套技术底座：Python 3.11 + FastAPI + aiohttp + YAML配置。这不是巧合——是有意设计的技术栈收敛：

| 组件 | AI Infra Gateway | SRE-LAB | AI Model Scheduler |
|------|-----------------|---------|-------------------|
| API框架 | FastAPI | N/A (K8s原生) | FastAPI |
| HTTP客户端 | aiohttp | N/A | aiohttp |
| 配置格式 | YAML | YAML (K8s manifests) | YAML |
| 限流算法 | Token Bucket | Traefik Middleware | Token Bucket (复用) |
| 熔断模式 | Circuit Breaker | PDB+Probe | Circuit Breaker (复用) |
| 监控方案 | pynvml+matplotlib | Prometheus+Grafana | Prometheus+matplotlib |

---

## 八、涉及的技术领域与原理

这三个项目横跨了多个技术领域。以下是我梳理的完整技术图谱：

### 8.1 操作系统与虚拟化

| 概念 | 在项目中的应用 |
|------|--------------|
| **VT-x/VMX虚拟化技术** | T-005五层诊断：硅层→微码→ACPI→BIOS→Windows内核 |
| **Windows服务管理** | Ollama作为后台进程的启动/停止/端口管理 |
| **环境变量作用域** | `$env:OLLAMA_HOST="127.0.0.1:11435"` 设置第二个Ollama实例 |
| **文件系统** | GGUF模型的blob格式、partial文件机制、Modelfile本地导入 |
| **网络栈** | TCP端口监听、socket连通性测试、DNS解析、GFW阻断机制 |

### 8.2 网络协议与API设计

| 概念 | 在项目中的应用 |
|------|--------------|
| **HTTP/1.1** | RESTful API设计（`GET /health`, `POST /api/chat`） |
| **SSE (Server-Sent Events)** | LLM流式输出的传输层协议 |
| **NDJSON (Newline Delimited JSON)** | Ollama的流式数据格式 |
| **Bearer Token认证** | 鉴权中间件，`Authorization: Bearer <token>` |
| **JWT (JSON Web Token)** | HS256签名的三段式Token，含过期机制 |
| **OpenAI API兼容** | `/v1/chat/completions` 端点格式 |

### 8.3 分布式系统

| 概念 | 在项目中的应用 |
|------|--------------|
| **限流 (Rate Limiting)** | 令牌桶算法：capacity=10, refill_rate=5/s |
| **熔断 (Circuit Breaking)** | 三态熔断器：CLOSED → OPEN → HALF_OPEN |
| **负载均衡 (Load Balancing)** | 加权轮询 / 最小连接 / 自适应评分 |
| **健康检查 (Health Check)** | 定时HTTP GET + 连续失败阈值 |
| **会话亲和性 (Session Affinity)** | session_id → backend_id 的映射表 |
| **服务发现 (Service Discovery)** | Backend Registry 从YAML加载或Prometheus SD发现 |
| **重试机制 (Retry)** | 可选的重试逻辑，带指数退避 |

### 8.4 云原生与容器编排

| 概念 | 在项目中的应用 |
|------|--------------|
| **Kubernetes** | K3S集群底座 |
| **StatefulSet** | Ollama推理引擎的有状态部署 |
| **Deployment** | Open WebUI的无状态部署 |
| **HPA (Horizontal Pod Autoscaler)** | 基于CPU/Memory的自动扩缩容 |
| **PDB (Pod Disruption Budget)** | 保证至少1个副本可用的优雅终止 |
| **PVC (Persistent Volume Claim)** | 模型文件的持久化存储 |
| **Ingress** | Traefik统一入口路由 |
| **Service (ClusterIP/Headless)** | 内网服务发现和负载均衡 |
| **Namespace** | 应用隔离（ai-platform、monitoring、logging） |
| **GitOps (ArgoCD)** | Git驱动部署，Auto Sync + Self Heal |
| **Sealed Secrets** | Git中的加密Secret存储 |

### 8.5 可观测性

| 概念 | 在项目中的应用 |
|------|--------------|
| **Metrics (指标)** | Prometheus采集GPU/应用/调度指标 |
| **Logging (日志)** | Loki + Promtail日志聚合 |
| **Tracing (链路)** | 暂无（两个项目都缺分布式追踪） |
| **Alerting (告警)** | AlertManager → WeChat Bot，11条Prometheus告警规则 |
| **Dashboard (仪表盘)** | pynvml+matplotlib GPU仪表盘、Scheduler仪表盘、Grafana可视化 |

### 8.6 AI/ML推理

| 概念 | 在项目中的应用 |
|------|--------------|
| **LLM推理引擎** | Ollama (本地)、vLLM (云端) |
| **GGUF模型格式** | llama.cpp的量化模型格式，文件头标识 `b'GGUF'` |
| **KV Cache** | Transformer推理中的Key-Value缓存，Session Affinity的目的 |
| **TTFT (首字延迟)** | 冷启动vs预热后，模型加载vs计算延迟的区分 |
| **TPOT (每字延迟)** | token生成速度，与模型参数量线性相关 |
| **Throughput (吞吐)** | 系统整体处理能力，受GPU算力+显存带宽约束 |
| **量化 (Quantization)** | Q4_K_M等量化方案，平衡模型大小和精度 |

### 8.7 性能工程

| 概念 | 在项目中的应用 |
|------|--------------|
| **异步编程 (asyncio)** | FastAPI + aiohttp的事件循环模型 |
| **连接池 (Connection Pool)** | aiohttp.TCPConnector 复用HTTP连接 |
| **并发vs并行** | asyncio的单线程并发模型 vs 多线程并行 |
| **压测方法论** | 并发梯度(1→2→4→8)、场景分层(light/medium/heavy)、指标采集 |
| **滚动窗口统计** | window_size=20的最近N个样本的平均值 |

---

## 九、踩坑全记录

### T-001: Ollama Registry被GFW阻断
- **现象**: `ollama pull` 卡在partial blob，`ollama list` 始终为空
- **根因**: `registry.ollama.ai` 被DNS污染+HTTPS SNI阻断
- **破局**: Python读取blob文件头→发现是完整GGUF→Modelfile本地导入
- **教训**: 环境体检必须包含外部依赖的网络可达性测试

### T-002: 网关8000端口无监听
- **现象**: `python gateway_server.py` 执行后 `curl localhost:8000` 连接拒绝
- **根因**: `Start-Process` 的CWD不是项目目录，相对路径 `config/gateway_config.yaml` 找不到
- **修复**: 创建 `start_gateway.py` 启动器，强制 `os.chdir()` + `sys.path` 绝对路径

### T-003: PowerShell终端stdout被IDE吞没
- **现象**: 在VS Code内置终端中运行脚本，stdout输出不显示
- **Workaround**: `cmd /c "command > output.txt 2>&1"` 重定向到文件再用read_file查看

### T-004: requirements.txt GBK编码冲突
- **现象**: `pip install -r requirements.txt` → `UnicodeDecodeError: 'gbk'`
- **根因**: Windows系统默认GBK编码，requirements.txt含中文注释
- **修复**: 移除所有中文注释，改为纯ASCII

### T-005: WSL2/Hyper-V不可用
- **现象**: 启用Hyper-V循环失败
- **诊断**: 五层深度诊断（硅层→ACPI→Hypervisor→VBS→MSR）
- **根因**: 机械革命OEM BIOS的ACPI `VirtualizationFirmwareEnabled` 标志位bug
- **结论**: 硬件层面的限制，无法通过软件修复

---

## 十、项目管理与工程思维

### 10.1 目录结构设计

三个项目采用了一致的目录组织方式：

```
XX-project/
├── README.md              ← 项目入口：架构、快速开始、关键数据
├── 01-核心模块/            ← 最重要的代码模块
│   ├── README.md          ←   模块自己的说明
│   ├── config/            ←   配置文件（YAML）
│   └── *.py               ←   源代码
├── 02-辅助模块/
├── 03-测试/压测/
├── 04-基础设施/监控/
├── docs/                  ← 深度文档集中存放
│   ├── ARCHITECTURE.md    ←   架构设计
│   ├── INTEGRATION.md     ←   集成指南
│   └── ...                ←   其他专题文档
└── LICENSE                ← 开源许可证
```

**设计原则**：
- `README.md` 是门面——提供最核心的信息（是什么、怎么跑、数据如何），不深入细节
- `docs/` 是深度——架构设计、决策理由、集成方案等放在这里
- 模块编号 `01-`, `02-` 暗示了优先级和学习顺序
- 每个模块有自己的 `config/` 目录，配置和代码放在一起

### 10.2 版本演进

AI Infra Gateway 经历了 v1.0 → v2.0 的升级：

| 功能 | v1.0 | v2.0 |
|------|------|------|
| 鉴权 | 硬编码API Key | JWT HS256 + API Key回退 |
| 弹性 | 无 | Circuit Breaker + 请求级重试 |
| 告警 | 无 | 6条Prometheus告警规则 |
| 压测 | 单模型 | 双模型C1-C8梯度对比 |
| 文档 | 平铺 | 4模块结构 + 文档索引 |

### 10.3 CHANGELOG

每个项目的CHANGELOG记录了版本变更，这是工程项目的基本素养——让接手的人知道发生了什么变化。

### 10.4 文件完整性思维

在 `AI_INFRA_SYSTEM_SUMMARY.md` 中，我用表格列出了每个项目的文件完整性——这不是为了"凑数"，而是为了"我自己知道交付了哪些东西，评审者也能一眼看清范围"。

---

## 十一、如果重新做一遍——复盘反思

### 11.1 做得好的

1. **环境体检前置**：虽然T-001未在网络层做体检（教训），但其他方面的体检（GPU、Python、Ollama路径）为后续开发节省了大量调试时间。
2. **技术栈收敛**：三个项目统一用 Python + FastAPI + aiohttp + YAML，减少了认知负担。
3. **Mock优先**：Mock vLLM后端让路由策略验证不需要真实云GPU，开发效率极大提升。
4. **文档即交付**：每个项目都有完整的README+架构+集成文档，不依赖"口口相传"。

### 11.2 可以改进的

1. **缺少单元测试**：三个项目都没有 `pytest` 测试用例。虽然手动验证过了，但没有自动化回归测试。
2. **缺少CI/CD Pipeline**：GitHub Actions可以为每次push自动跑lint和test。
3. **分布式追踪**：三项目都缺Jaeger/Zipkin式的分布式追踪，跨后端调用的全链路耗时不可见。
4. **Scheduler的配置热更新**：目前修改 `scheduler_config.yaml` 需要重启，应该支持热加载。
5. **数据库持久化Scheduler状态**：Scheduler的后端评分、Session映射都在内存中，重启就丢失。

### 11.3 延伸方向

1. **K8s Service Discovery**：Scheduler自动发现K3S集群中新增的Ollama Pod并通过Prometheus SD注册。
2. **GPU-Aware Scheduling**：Scheduler感知每个后端的GPU型号（RTX 4060 vs RTX 4090）和VRAM余量，做更精细的调度。
3. **Model Preloading**：根据历史请求模式，预加载常用模型到GPU显存，降低冷启动TTFT。
4. **Multi-Cluster Federation**：跨多个K3S集群的联邦调度。

---

## 附录：项目全貌速览

### 三项目对比

| 维度 | AI Infra Gateway | SRE-LAB | AI Model Scheduler |
|------|-----------------|---------|-------------------|
| **代码行数** | ~2600行 Python | ~2000行 YAML | ~1200行 Python |
| **源文件数** | 23 | 13+ | 7 |
| **端口** | :8000 (网关), :9090 (仪表盘) | :80 (Traefik) | :9000 (调度), :9010 (仪表盘) |
| **GPU需求** | RTX 4060 (必须有) | 可扩展 | 无（纯CPU+网络） |
| **容器化** | ❌ | ✅ K3S | N/A |
| **核心算法** | Token Bucket, Circuit Breaker | HPA, PDB, GitOps | 5种路由策略, Adaptive Score |
| **开发时间** | ~4小时 | ~8小时 | ~8小时 |

### 代码总量

- AI Infra Gateway: ~2600行 Python
- SRE-LAB: ~2000行 YAML配置
- AI Model Scheduler: ~1200行 Python
- **总计: ~5000行代码 + ~2000行配置 = ~7000行**

---

*日志由独立开发者撰写于2026年6月29日（v2版：新增多节点生产架构部署章节）*
*三项目已形成从"裸金属推理"→"云原生平台"→"统一异构调度"的完整成长闭环*