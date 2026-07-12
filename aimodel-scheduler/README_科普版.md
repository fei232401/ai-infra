# 🧠 AI Model Scheduler — 小白零基础完全科普

> 本文档的目标：**让完全不懂技术的人也能看懂本项目在做什么，每个术语什么意思，甚至能自己动手复现。**

---

## 📌 一句话说清楚：这个项目是干啥的？

假设你有好几台"AI 机器人"（叫**推理后端**），分布在不同的地方：

- 一台在你自己的笔记本电脑上（慢但免费）
- 一台在你家里的服务器上（中等速度）
- 一台在云端的超级计算机上（快但花钱）

**AI Model Scheduler** 就像一个**智能总机话务员**。你只需要打一个电话（发一个 HTTP 请求），话务员会自动判断：

- 谁现在有空？（健康检查 + 负载均衡）
- 谁最适合干这个活？（路由策略）
- 谁最便宜？谁最快？（成本感知/延迟优先）

你不用管背后有几台机器、每台机器在哪、是什么型号——**统一入口，智能调度**。

---

# 🧱 第一部分：基础概念扫盲（从零开始）

## 1. 什么是 AI 模型推理？

- **训练**：拿海量数据"教"AI 学会说话/画图/写代码（像读书考大学）
- **推理（Inference）**：训练好的 AI 实际回答你的问题（像毕业了开始工作）
- 本项目只做**推理阶段**的事情

## 2. 什么是 GPU？（显卡）

- GPU = Graphics Processing Unit（图形处理器），俗称**显卡**
- 普通显卡：玩游戏用（比如 RTX 4060、RTX 4090）
- AI 显卡：做计算用，因为 AI 需要大量并行计算，GPU 最擅长
- 关键指标：**显存**（VRAM）越大，能跑的模型越大
  
| 显卡型号 | 显存 | 能跑什么模型 | 速度 |
|---------|------|-------------|------|
| RTX 4060 Laptop（笔记本） | 8GB | 小模型（0.5B~7B） | 中等 |
| RTX 4090（台式机/云端） | 24GB | 大模型（7B~70B） | 非常快 |

> B = Billion（十亿参数），参数越多越聪明，但也越吃显存

## 3. 什么是 Ollama？

- **Ollama** 是一个"一键运行 AI 模型"的工具
- 你只需要在命令行输入 `ollama run qwen2.5`，它就会自动下载并启动 AI 模型
- 它会在你的电脑上开一个**端口**（默认 11434），其他程序可以通过这个端口调用 AI
- 类比：Ollama 就像你的私人 AI 服务器，安装即用

## 4. 什么是 vLLM？

- vLLM = Virtual Large Language Model
- 和 Ollama 类似，也是运行 AI 模型的工具
- **优势**：吞吐量极高（每秒能处理的请求更多），适合**生产环境**
- 通常部署在**云服务器**上，配合高端 GPU（如 RTX 4090、A100）
- 本项目有一个 `mock_backend.py`，专门**模拟** vLLM 的行为，让你没有云 GPU 也能测试

## 5. 什么是端口（Port）？

- 端口就像一台电脑上的"门牌号"
- IP 地址是"这栋楼在哪"，端口是"哪个房间"
- 比如 `localhost:11434` 就是：本地电脑（localhost）+ 房间号 11434
- 不同程序用不同端口，不会冲突：

| 程序 | 端口号 | 用途 |
|------|--------|------|
| Ollama A（第一个实例） | 11434 | 运行 qwen2.5:0.5b 模型 |
| Ollama B（第二个实例） | 11435 | 运行 qwen2.5:1.5b 模型 |
| Mock vLLM | 11436 | 模拟云 GPU |
| **Scheduler 主入口** | **9000** | **统一入口** |
| 仪表盘 | 9010 | 可视化监控 |

## 6. 什么是 HTTP 和 REST API？

- HTTP = 电脑之间"说话"的协议（超文本传输协议）
- REST API = 一种约定好的"说话方式"
- 常见的"动词"（方法）：

| 方法 | 意思 | 类比 |
|------|------|------|
| GET | 获取数据 | "查一下" |
| POST | 提交数据 | "帮我做件事" |
| PUT | 更新数据 | "改一下" |
| DELETE | 删除数据 | "删掉" |

- 本项目主要用 `POST`：你把问题发过去，AI 把回答返回给你

## 7. 什么是 JSON？

- JSON 是一种**数据格式**，长得像这样：

```json
{
  "model": "qwen2.5:0.5b",
  "messages": [
    {"role": "user", "content": "你好"}
  ]
}
```

- 前端和后端、后端和后端之间传递数据都用 JSON
- 人类可读，电脑也能解析

## 8. 什么是 SSE 流式传输？

- SSE = Server-Sent Events（服务器推送事件）
- 传统请求：你问 → AI 想半天 → 一次性把整个答案给你 → 你干等
- 流式传输：你问 → AI 边想边回答 → 一个字一个字往外蹦 → 你看到打字效果
- 用户体验更好，感觉 AI 在"实时思考"
- 浏览器原生支持，不需要额外库

---

# 🏗️ 第二部分：项目架构科普

## 9. 什么是"三层架构"？

本项目是 AI Infra 体系的**第三层**：

```
应用层     AI Model Scheduler（智能调度层）    ← 本项目
           ↓                    ↓
中间层     AI Infra Gateway    SRE-LAB
           (本地裸金属)          (K3S集群)
           ↓                    ↓
硬件层     笔记本 RTX 4060      云 GPU RTX 4090 / K3S 集群
```

**每一层的职责：**

| 层级 | 名称 | 负责什么 | 类比 |
|------|------|---------|------|
| 底层 | 硬件/后端 | 实际运行 AI 模型的机器 | 餐厅的厨房 |
| 中间层 | 推理后端 | 管理模型、接受请求、返回结果 | 厨师 |
| 顶层 | 调度层（本项目） | 决定请求发给谁、怎么做负载均衡 | 餐厅经理/总台 |

## 10. 什么是 Bare Metal（裸金属）？

- 直译：**裸机**，指真实物理机器（不是虚拟机）
- 在本项目中 = 你自己的笔记本电脑
- 特点：性能稳定，没有虚拟化开销，但资源有限
- AI Infra Gateway 就是跑在 Windows 裸金属上的推理网关

## 11. 什么是 K3S 集群？

- K3S = 轻量版 Kubernetes（k8s），Kubernetes 的首尾字母+中间8个字母
- **Kubernetes（k8s）**：业界最流行的容器编排平台，自动管理容器的部署、扩缩容、负载均衡
- **K3S**：精简版，适合边缘计算、IoT 设备、资源有限的服务器
- 在本项目中，SRE-LAB 就是在 K3S 上跑的推理平台
- 优势：
  - **自动扩缩容（HPA）**：访问量大时自动增加实例，访问量小时自动减少
  - **自动恢复**：某个实例挂了，自动重启
  - **声明式配置（GitOps）**：通过 Git 仓库管理配置，改代码自动生效

## 12. 什么是 Cloud GPU？（云显卡）

- 你不需要自己买昂贵的显卡，而是租云服务商的 GPU
- 常见服务商：AutoDL、阿里云、AWS、Google Cloud
- 优势：按小时付费，需要时租用，用完释放
- 本项目示例使用 AutoDL 的 RTX 4090（24GB 显存）

---

# ⚙️ 第三部分：核心组件科普

## 13. FastAPI（API 框架）

- **FastAPI** = 一个 Python 写的 Web 框架
- 专门用来写 API 接口，特点是**快**（性能高 + 开发快）
- 自动生成 API 文档（访问 `/docs` 就能看到漂亮的交互式文档）
- 本项目用 FastAPI 搭建了两个服务：
  - `unified_gateway.py` → Scheduler 主入口（端口 9000）
  - `mock_backend.py` → Mock vLLM 后端（端口 11436）
  - `dashboard.py` → 仪表盘（端口 9010）

## 14. aiohttp（异步 HTTP 客户端）

- aiohttp = Async IO HTTP（异步输入输出 HTTP 库）
- **同步**：发一个请求，干等着直到结果回来（像打电话）
- **异步**：发完请求继续做别的事，结果回来再处理（像发微信）
- 异步的好处：同样的时间内能处理更多请求
- 本项目用 aiohttp 在 Scheduler 和各后端之间转发请求

## 15. BackendRegistry（后端注册表）

- **什么是注册表？** 记录所有"可用后端"信息的清单
- 就像一个**通讯录**，记录每个后端：
  - 名字（ollama-local-a）
  - 地址（127.0.0.1:11434）
  - 有什么模型（qwen2.5:0.5b）
  - 当前是否健康
  - 历史表现评分

- **健康检查（Health Check）**：每 10 秒挨个给后端发"你还好吗？"的请求
  - 如果后端回复"我很好" → 标记为 HEALTHY
  - 如果连续 3 次不回复 → 标记为 UNHEALTHY

## 16. Circuit Breaker（断路器）

- 灵感来自电路中的**保险丝/断路器**
- 三种状态：

```
CLOSED（闭合）→ 正常工作
   ↓ 连续失败 3 次
OPEN（断开）→ 拒绝请求，让后端"休息"
   ↓ 等待 30 秒
HALF_OPEN（半开）→ 放一个测试请求进来
   ↓ 成功 → 回到 CLOSED
   ↓ 失败 → 回到 OPEN
```

- 作用：**防止雪崩效应**——一个后端挂了，不停重试只会让情况更糟
- 断路器让它先"冷静"一下，再尝试恢复

## 17. 负载均衡（Load Balancer）

- **什么是负载？** 请求的压力
- **什么是均衡？** 均匀分配
- **负载均衡**：把请求合理分配给各个后端，避免"有的人累死，有的人闲死"

本项目的三种负载均衡算法：

| 算法 | 怎么选后端 | 适合场景 |
|------|-----------|---------|
| **加权轮询（Weighted RR）** | 按权重随机选（权重高的被选中概率大） | 后端性能差异大的场景 |
| **最小连接（Least Connections）** | 选当前正在处理的请求最少的 | 请求耗时差异大的场景 |
| **自适应评分（Adaptive Score）** | 动态算分，综合延迟+错误率+并发量 | 最智能，也是默认策略 |

**自适应评分的公式：**

```
score = weight × (1 - error_rate) / (ttft_normalized × concurrency_penalty)

其中：
- weight: 配置权重（mock-vllm 权重 2，Ollama 权重 1）
- error_rate: 错误率
- ttft_normalized: 首 token 延迟归一化（以 500ms 为基准）
- concurrency_penalty: 并发惩罚（越接近上限得分越低）
```

## 18. 路由策略（Routing Strategy）

- **路由** = 决定请求发到哪个后端
- 比负载均衡更高一层：先决定"方向"，再决定"具体给谁"

本项目的五种路由策略：

| 策略 | 触发方式 | 做什么 | 类比 |
|------|---------|--------|------|
| **模型感知** | 请求带 `model` 参数 | 只路由到有这个模型的后端 | "谁负责川菜就去谁那" |
| **延迟优先** | 头部 `X-Routing-Strategy: latency` | 选历史首字延迟最低的 | "谁打字快就用谁" |
| **吞吐优先** | 头部 `X-Routing-Strategy: throughput` | 选每秒处理最多请求的 | "谁手脚麻利用谁" |
| **成本感知** | 头部 `X-Routing-Strategy: cost` | 选每次调用最便宜的 | "谁便宜用谁" |
| **亲和性** | 请求带 `session_id` | 同一会话始终发到同一后端 | "上次谁服务的还是谁" |

**执行顺序**（优先级）：
```
1. 按 model 字段过滤（模型感知）
2. 过滤掉不健康的后端
3. 过滤掉断路器打开的后端
4. 检查是否有亲和性规则（session_id）
5. 根据策略选择（latency / throughput / cost / weighted）
6. 用负载均衡算法从候选池中选一个
```

---

# 🛡️ 第四部分：安全与防护科普

## 19. 什么是鉴权（Authentication）？

- **鉴权** = 验证"你是谁"
- 本项目用 **Bearer Token** 方式：
  - 每个请求的头部带上 `Authorization: Bearer test-key`
  - 服务器检查这个 key 是否在允许列表里
  - 相当于**会员卡**——有卡才能进

## 20. 什么是限流（Rate Limiting）？

- **限流** = 限制单位时间内能发多少请求
- 防止某个用户"霸占"所有资源
- 本项目用 **Token Bucket（令牌桶）** 算法：
  - 有一个桶，里面装令牌
  - 每处理一个请求，消耗一个令牌
  - 令牌每秒自动补充（比如每秒补充 10 个）
  - 没令牌了 → 请求被拒绝（HTTP 429 Too Many Requests）

**两层限流**：
| 级别 | 限制 | 防止什么 |
|------|------|---------|
| 全局 | 每秒 10 个请求 | 单个客户端打崩整个系统 |
| 每后端 | 每秒 10 个请求 | 单个后端被请求淹没 |

## 21. 什么是 429、502、503 状态码？

| 状态码 | 含义 | 出现场景 |
|--------|------|---------|
| 200 | 成功 | 请求正常处理 |
| 401 | 未授权 | API Key 不对 |
| 429 | 请求过多（限流） | 发请求太快了 |
| 502 | 后端挂了 | 后端无响应 |
| 503 | 服务不可用 | 所有后端都不健康 |
| 504 | 超时 | 后端响应太慢 |

---

# 📊 第五部分：可观测性科普

## 22. 什么是可观测性（Observability）？

- 简单说 = **能看到系统里面在发生什么**
- 三大支柱：

| 支柱 | 是什么 | 本项目的实现 |
|------|--------|-------------|
| **日志（Logs）** | 事件记录 | Python logging 模块（带时间戳） |
| **指标（Metrics）** | 数值统计 | TTFT、TPS、错误率等 |
| **追踪（Tracing）** | 请求链路 | OpenTelemetry（可选） |

## 23. 什么是 Prometheus？

- **Prometheus** = 开源监控系统和时序数据库
- 定期"抓取"各个服务的指标数据
- 有强大的查询语言（PromQL）
- 本项目暴露 `/v1/metrics` 端点给 Prometheus 采集

## 24. 什么是仪表盘（Dashboard）？

- **仪表盘** = 可视化界面，实时展示系统状态
- 本项目的仪表盘（dashboard.py）展示：

| 卡片 | 显示内容 |
|------|---------|
| 总请求数 | 从启动到现在一共处理了多少请求 |
| 成功率 | 成功请求的百分比 |
| 运行时间 | 已经运行了多久 |
| 活跃会话 | 当前有几组活跃的会话 |

**后端表格**：每个后端一行，显示：
- 名称 + 引擎类型（Ollama / vLLM / Mock）
- 健康状态（颜色编码：绿=健康，红=不健康，黄=断路器打开）
- TTFT（首 token 延迟，越低越好）
- TPS（每秒处理 token 数，越高越好）
- 活跃连接 / 最大连接
- 错误次数

## 25. 什么是 TTFT 和 TPS？

| 缩写 | 全称 | 中文 | 含义 | 好还是坏 |
|------|------|------|------|---------|
| TTFT | Time To First Token | 首 Token 延迟 | 从发请求到收到第一个字的时间 | **越低越好** |
| TPS / Throughput | Tokens Per Second | 每秒 Token 数 | 每秒能生成多少个字 | **越高越好** |
| TPOT | Time Per Output Token | 每 Token 生成时间 | 生成每个字需要多少毫秒 | **越低越好** |

**典型数值对比**：

| 后端 | TTFT | TPOT | 速度体验 |
|------|------|------|---------|
| Ollama 本地 (4060) | 100~500ms | 20~50ms/字 | 中等 |
| Mock vLLM | 30~80ms | 5ms/字 | 飞快（模拟云 GPU） |
| 真实 vLLM (4090) | 20~50ms | 3~5ms/字 | 极快 |

---

# 🔧 第六部分：配置与部署科普

## 26. 什么是 YAML？

- YAML = 一种配置文件格式，设计目标是**人类可读**
- 本项目的配置文件 `scheduler_config.yaml` 长这样：

```yaml
scheduler:
  port: 9000
  health_check_interval: 10  # 每10秒检查一次后端健康

backends:
  - name: ollama-local-a
    url: http://127.0.0.1:11434
    models:
      - qwen2.5:0.5b
    max_concurrency: 8

strategies:
  model_aware: true
  latency: true
  throughput: true
  cost: true
  affinity: true
```

- 缩进用**空格**（不能用 Tab），缩进表示层级关系
- 类比：有点像"填空题"的模板，填好就能用

## 27. 怎么启动整个系统？

一共需要开 **4 个终端窗口**：

```
┌─────────────────────────────────────────────────────────┐
│ 终端1: Ollama A (已默认运行)                              │
│ > ollama serve                                          │
│ → 在端口 11434 运行 qwen2.5:0.5b                         │
├─────────────────────────────────────────────────────────┤
│ 终端2: Ollama B                                         │
│ > $env:OLLAMA_HOST="127.0.0.1:11435"                    │
│ > ollama serve                                          │
│ → 在端口 11435 运行 qwen2.5:1.5b                         │
├─────────────────────────────────────────────────────────┤
│ 终端3: Mock vLLM (模拟云GPU)                              │
│ > cd 01-scheduler-core                                  │
│ > python mock_backend.py                                │
│ → 在端口 11436 模拟云 GPU 的低延迟                          │
├─────────────────────────────────────────────────────────┤
│ 终端4: Scheduler 主入口                                   │
│ > cd 02-unified-api                                     │
│ > python unified_gateway.py                             │
│ → 在端口 9000 提供统一 API 入口                            │
└─────────────────────────────────────────────────────────┘
```

再加上可选的：
```
┌─────────────────────────────────────────────────────────┐
│ 终端5: 仪表盘                                            │
│ > cd 04-observability                                   │
│ > python dashboard.py                                   │
│ → 在浏览器打开 http://localhost:9010                      │
└─────────────────────────────────────────────────────────┘
```

---

# 🧪 第七部分：动手实践指南（小白复现步骤）

## 需要的硬件

| 最低要求 | 推荐配置 |
|---------|---------|
| 任何有 GPU 的电脑 | NVIDIA RTX 4060 或更高 |
| 8GB 显存 | 16GB+ 显存 |
| Windows 11 或 Linux | 同左 |
| 50GB 磁盘空间 | 100GB+ SSD |

## 步骤 1：安装 Python 3.11+

1. 去 https://www.python.org/downloads/ 下载 Python 3.11 或更高版本
2. 安装时**务必勾选** "Add Python to PATH"
3. 验证：打开终端（cmd/powershell），输入 `python --version`，看到版本号就对了

## 步骤 2：安装 Ollama

1. 去 https://ollama.com/download 下载安装包
2. 安装后打开终端，输入 `ollama pull qwen2.5:0.5b` 下载小模型
3. 再输入 `ollama pull qwen2.5:1.5b` 下载稍大的模型
4. 验证：输入 `ollama list` 能看到已下载的模型列表

## 步骤 3：下载本项目代码

```powershell
# 或从 GitHub 克隆
git clone https://github.com/你的用户名/aimodel-scheduler.git
cd aimodel-scheduler
```

## 步骤 4：安装依赖

```powershell
pip install -r requirements.txt
```

`requirements.txt` 里包含：
- **fastapi** — Web 框架
- **uvicorn** — 启动服务器
- **aiohttp** — 异步 HTTP 请求
- **pyyaml** — 读取 YAML 配置
- **pyjwt** — JWT 鉴权
- **matplotlib** — 画图（仪表盘用）

## 步骤 5：启动后端和调度器

**开 4 个终端窗口**，按顺序执行：

**终端 1：启动 Ollama A（默认端口 11434）**
```powershell
ollama serve
```
看到 `Listening on 127.0.0.1:11434` 说明成功

---

**终端 2：启动 Ollama B（端口 11435）**
```powershell
$env:OLLAMA_HOST="127.0.0.1:11435"
& "C:\Users\admin\AppData\Local\Programs\Ollama\ollama.exe" serve
```
> 注意：路径中的 `admin` 改成你的用户名

---

**终端 3：启动 Mock vLLM（模拟云 GPU）**
```powershell
cd aimodel-scheduler\01-scheduler-core
python mock_backend.py
```
看到 `Mock vLLM backend running on :11436` 说明成功

---

**终端 4：启动 Scheduler（统一入口）**
```powershell
cd aimodel-scheduler\02-unified-api
python unified_gateway.py
```
看到 `Unified API running on http://localhost:9000` 说明成功

---

**可选：仪表盘**
```powershell
cd aimodel-scheduler\04-observability
python dashboard.py
```
浏览器打开 `http://localhost:9010` 就能看到实时状态

## 步骤 6：发送你的第一个请求

```powershell
curl -X POST http://localhost:9000/v1/chat/completions `
  -H "Content-Type: application/json" `
  -H "Authorization: Bearer test-key" `
  -d '{"model":"qwen2.5:0.5b","messages":[{"role":"user","content":"你好"}]}'
```

**解释每个参数**：
| 参数 | 含义 |
|------|------|
| `-X POST` | 用 POST 方法发送请求 |
| `-H "Content-Type: application/json"` | 告诉服务器我发的是 JSON |
| `-H "Authorization: Bearer test-key"` | 我的会员卡号是 test-key |
| `-d '{...}'` | 请求体：用什么模型 + 问什么问题 |

**成功时你会看到类似**：
```json
{
  "model": "qwen2.5:0.5b",
  "choices": [
    {"message": {"content": "你好！有什么可以帮助你的吗？"}}
  ]
}
```

**响应头部会显示路由信息**：
```
X-Routed-Backend: ollama-local-a     ← 这个请求发给了谁
X-Routing-Strategy: model_aware      ← 用了什么策略
```

## 步骤 7：测试不同的路由策略

**延迟优先（选最快的后端）**：
```powershell
curl -X POST http://localhost:9000/v1/chat/completions `
  -H "Authorization: Bearer test-key" `
  -H "X-Routing-Strategy: latency" `
  -d '{"model":"qwen2.5:0.5b","messages":[{"role":"user","content":"你好"}]}'
```

**会话亲和性（同一会话走同一后端）**：
```powershell
curl -X POST http://localhost:9000/v1/chat/completions `
  -H "Authorization: Bearer test-key" `
  -H "X-Session-Id: my-session-123" `
  -d '{"model":"qwen2.5:0.5b","messages":[{"role":"user","content":"你好"}]}'
```

## 步骤 8：运行压测（看看系统能承受多大压力）

```powershell
cd aimodel-scheduler\03-scheduler-benchmark

# 调度器压力测试（1/2/4/8/16 并发梯度）
python scheduler_stress_test.py

# 多后端对比测试
python cross_backend_compare.py
```

**输出示例**：
```
===== 并发级别: 8 =====
成功: 20/20 (100%)
P50延迟: 342ms | P99延迟: 891ms
吞吐量: 23.4 RPS
后端分布:
  ollama-local-a: 12 (60%)
  ollama-local-b: 4 (20%)
  mock-vllm: 4 (20%)
```

**解释**：
- 20 个请求全部成功（100%）
- 一半的请求在 342ms 内完成（P50）
- 99% 的请求在 891ms 内完成（P99）
- 每秒能处理 23.4 个请求（RPS）

---

# 💡 第八部分：常见问题（小白 FAQ）

## Q1：我只有一台电脑，能跑这个项目吗？

**能。** 本项目设计就是在**单机上**模拟多后端。你只需要：
1. 开两个 Ollama 实例（不同端口）
2. 运行 mock_backend.py（模拟云 GPU）
3. 运行 scheduler

总共三台"虚拟后端"都在你自己电脑上。

## Q2：Ollama B 怎么启动？和 A 有什么区别？

```powershell
# 先设置环境变量，告诉 Ollama 用不同端口
$env:OLLAMA_HOST="127.0.0.1:11435"

# 再启动
ollama serve
```

区别：
| | Ollama A | Ollama B |
|--|---------|---------|
| 端口 | 11434（默认） | 11435 |
| 模型 | qwen2.5:0.5b（5亿参数） | qwen2.5:1.5b（15亿参数） |
| 速度 | 更快 | 更慢但更聪明 |

## Q3：mock_backend.py 真的在跑 AI 吗？

**没有。** 它是一个"模拟器"（Mock），只是假装自己是 AI 后端：
- 收到请求后，等 30~80ms（模拟 vLLM 的延迟）
- 然后返回预设好的回答模板（不是真正的 AI 生成）
- 目的是**在没有云 GPU 的情况下**，测试调度器的路由、负载均衡、限流等功能

## Q4：什么是"响应式"返回和"流式"返回？

**响应式（非流式）**：
```json
{
  "choices": [{"message": {"content": "你好！今天天气很好，有什么可以帮你的？"}}]
}
```
→ AI 全部想好了，一次性给你，你**等待时间长**但**拿到完整结果**

**流式（SSE）**：
```
data: {"choices":[{"delta":{"content":"你"}}]}
data: {"choices":[{"delta":{"content":"好"}}]}
data: {"choices":[{"delta":{"content":"！"}}]}
...
```
→ AI 一个字一个字给你，你**立刻看到第一个字**，但**逐个接收**

## Q5：我要怎么修改配置？

编辑 `01-scheduler-core/config/scheduler_config.yaml`：

**添加新的后端**：
```yaml
backends:
  - name: ollama-local-c        # 名字
    url: http://127.0.0.1:11436  # 地址
    models:                      # 能跑的模型
      - qwen2.5:3b
    max_concurrency: 8           # 最大并发数
    weight: 1                    # 权重（越高越优先）
    cost_per_token: 0.0          # 成本（每token价格）
```

**修改限流**：
```yaml
rate_limit:
  global:
    capacity: 20       # 桶容量
    fill_rate: 10      # 每秒补充令牌数
```

**开启/关闭路由策略**：
```yaml
strategies:
  model_aware: true    # 开启模型感知
  latency: false       # 关闭延迟优先
  cost: true           # 开启成本感知
```

## Q6：报错 "Address already in use" 怎么办？

说明端口被占用了。可能原因：
1. 已经启动了一个程序占着同一个端口
2. 之前的程序没关干净

**解决**：
```powershell
# 查看谁占用了端口
netstat -ano | findstr :11434

# 找到 PID（最后一列），杀掉进程
taskkill /PID 1234 /F
```

## Q7：怎么在浏览器里测试 API？

启动 Scheduler 后，打开：
- `http://localhost:9000/docs` → Swagger 交互式 API 文档
- `http://localhost:9000/redoc` → 另一种风格的文档

在 Swagger 页面里，你可以：
1. 点击 "Authorize" 输入 `test-key`
2. 找到 `/v1/chat/completions` 端点
3. 点击 "Try it out"
4. 填入参数，点击 "Execute"
5. 直接看到请求结果

不需要用命令行 curl！

## Q8：为什么我要学这个项目？学了有什么用？

| 你能学到 | 应用场景 |
|---------|---------|
| AI 模型部署和推理 | 了解 AI 从训练到上线的完整流程 |
| 微服务架构设计 | 网关、调度、负载均衡、熔断等企业级模式 |
| 异步编程（asyncio） | 高并发网络编程 |
| 容器化和云原生（K3S） | 现代 DevOps 实践 |
| 可观测性体系 | 监控、告警、仪表盘 |
| API 设计和安全 | REST API、鉴权、限流 |

**一句话总结**：这是一个**缩小版的企业级 AI 推理调度系统**，掌握了它，你就掌握了 AI 基础设施的核心架构设计思路。

---

# 📚 第九部分：术语速查表

| 术语 | 英文 | 一句话解释 |
|------|------|-----------|
| 推理 | Inference | AI 用学到的知识回答问题 |
| 模型 | Model | 训练好的 AI"大脑" |
| 参数 | Parameter | 模型的"知识量"，越多越聪明 |
| GPU | 显卡 | AI 计算的加速器 |
| 显存 | VRAM | GPU 的内存，决定能跑多大模型 |
| 端口 | Port | 电脑上的"门牌号" |
| HTTP | 超文本传输协议 | 电脑之间通信的"语言" |
| API | 应用程序接口 | 程序之间调用的"接口" |
| JSON | 数据格式 | 电脑之间传数据的"格式" |
| SSE | 服务器推送事件 | AI 一个字一个字往外蹦的技术 |
| 负载均衡 | Load Balancing | 把请求均匀分配给各个后端 |
| 断路器 | Circuit Breaker | 保护后端不被请求淹死的机制 |
| 限流 | Rate Limiting | 限制单位时间内的请求数 |
| 鉴权 | Authentication | 验证"你是谁" |
| 路由 | Routing | 决定请求发给哪个后端 |
| 健康检查 | Health Check | 定期检查后端是否活着 |
| 延迟 | Latency | 从请求到响应的时间 |
| 吞吐量 | Throughput | 单位时间能处理多少请求 |
| 仪表盘 | Dashboard | 可视化监控面板 |
| YAML | 配置文件格式 | 人类可读的配置文件 |
| K3S | 轻量 Kubernetes | 容器编排平台（精简版） |
| HPA | 自动扩缩容 | 负载高时自动加机器 |
| GitOps | Git 运维 | 用 Git 管理配置和部署 |
| Prometheus | 监控系统 | 采集和查询指标的框架 |
| OpenTelemetry | 可观测性框架 | 追踪请求在系统中的完整路径 |

---

> **最后的话**：这个项目虽然看起来术语很多，但本质上就是"一个聪明的总机，连接多个 AI 服务员"。不要被术语吓到——每个术语背后都是一个简单的生活类比。动手跟着"第七部分"的步骤走一遍，你就全明白了！
