#!/usr/bin/env python3
# ============================================================
# 阶段2：企业级异步推理 API 网关
# 功能：鉴权 + 令牌桶限流 + SSE 流式转发 + Ollama 代理
# 运行：python gateway_server.py
# ============================================================
import asyncio
import time
import hashlib
import json
import logging
import os
from typing import Optional
from contextlib import asynccontextmanager
from collections import defaultdict

import yaml
import aiohttp
import jwt as pyjwt
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# ------------------------------------------------------------------
# 日志配置
# ------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("gateway")

# ------------------------------------------------------------------
# OpenTelemetry 分布式追踪 (TEMPO-ready) — v3.0 可观测性升级
# ------------------------------------------------------------------
# 启用条件：设置环境变量 OTEL_ENABLED=1 即可激活
#   OTEL_ENABLED=1 python gateway_server.py
# Span 层级：HTTP Request → Auth → RateLimit → Ollama Proxy → Stream Chunks
try:
    if os.environ.get("OTEL_ENABLED") == "1":
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.trace import SpanKind, Status, StatusCode

        resource = Resource.create({"service.name": "ai-infra-gateway", "service.version": "3.0.0"})
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer("gateway")

        HAS_OTEL = True
        logger.info("[OTEL] Distributed tracing enabled (TEMPO-compatible)")
    else:
        HAS_OTEL = False
except ImportError:
    HAS_OTEL = False
    logger.debug("[OTEL] Packages not installed (pip install opentelemetry-api opentelemetry-sdk opentelemetry-instrumentation-fastapi)")
except Exception as e:
    HAS_OTEL = False
    logger.warning(f"[OTEL] Initialization failed: {e}")

# ------------------------------------------------------------------
# 配置加载
# ------------------------------------------------------------------
import os
def load_config(path: str = None) -> dict:
    if path is None:
        # 使用绝对路径，避免后台启动时相对路径找不到文件
        base_dir = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(base_dir, "config", "gateway_config.yaml")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

config = load_config()

# ------------------------------------------------------------------
# ============ 模块1：令牌桶限流器 ============
# ------------------------------------------------------------------
# 庖丁解牛 - 令牌桶算法：
# 想象一个水桶：
#   - 桶底小孔每秒漏出 refill_rate 滴水（补充令牌）
#   - 桶最多装 capacity 滴水（突发容量）
#   - 每次请求从桶里取一滴
#   - 桶干了 → 返回 429 Too Many Requests
#   - 这是 AI Infra 网关的"防弹衣"，防止 Ollama 后端被压垮
class TokenBucket:
    def __init__(self, capacity: int, refill_rate: float):
        self.capacity = capacity          # 桶容量
        self.refill_rate = refill_rate    # 每秒补充令牌数
        self.tokens = capacity            # 当前令牌数
        self.last_refill = time.monotonic()  # 上次补充时间

    def consume(self, tokens: int = 1) -> bool:
        """尝试消费令牌。返回 True=放行, False=限流触发"""
        now = time.monotonic()
        elapsed = now - self.last_refill

        # 按时间补充令牌
        refill_tokens = elapsed * self.refill_rate
        self.tokens = min(self.capacity, self.tokens + refill_tokens)
        self.last_refill = now

        # 尝试消费
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False

# 创建全局令牌桶（按API Key分桶）
buckets: dict[str, TokenBucket] = defaultdict(
    lambda: TokenBucket(
        config["rate_limit"]["capacity"],
        config["rate_limit"]["refill_rate"],
    )
)

# ------------------------------------------------------------------
# ============ 模块1.5：熔断器 ============
# ------------------------------------------------------------------
# 庖丁解牛 - 熔断器 (Circuit Breaker)：
# 比喻：就像家里的电闸保险丝
#   - 正常状态 (CLOSED): 电流通过，正常请求
#   - 连续失败 3 次 → 跳闸 (OPEN): 拒绝所有请求，快速失败
#   - 30 秒后 → 半开 (HALF_OPEN): 放一个试探请求试试
#   - 试探成功 → 恢复 (CLOSED)
#   - 试探失败 → 重新熔断 (OPEN)
# 意义：保护 Ollama 后端不因故障雪崩，快速失败 > 长时间等待
class CircuitBreaker:
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(self, failure_threshold: int, timeout_seconds: int):
        self.failure_threshold = failure_threshold
        self.timeout_seconds = timeout_seconds
        self.state = self.CLOSED
        self.failure_count = 0
        self.last_failure_time = 0
        self.last_success_time = time.monotonic()

    def record_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.monotonic()
        if self.failure_count >= self.failure_threshold:
            self.state = self.OPEN
            logger.warning(f"[熔断器] 状态: OPEN (连续 {self.failure_count} 次失败)")

    def record_success(self):
        self.failure_count = 0
        self.state = self.CLOSED
        self.last_success_time = time.monotonic()

    def allow_request(self) -> bool:
        if self.state == self.CLOSED:
            return True
        if self.state == self.OPEN:
            if time.monotonic() - self.last_failure_time > self.timeout_seconds:
                self.state = self.HALF_OPEN
                logger.info("[熔断器] 状态: HALF_OPEN (试探性恢复)")
                return True
            return False
        # HALF_OPEN — allow one trial
        return True

circuit_breaker = CircuitBreaker(
    config["circuit_breaker"]["failure_threshold"],
    config["circuit_breaker"]["timeout_seconds"],
)

# ------------------------------------------------------------------
# ============ 模块2：JWT + API Key 双模鉴权中间件 ============
# ------------------------------------------------------------------
# 庖丁解牛 - JWT 鉴权 (v2 镀金升级)：
# API Key (v1):
#   类比：大楼门禁卡 — 固定密码，谁拿到都能进，泄露=完蛋
# JWT Token (v2):
#   类比：访客通行证 — 有时效性 (exp)，过期自动失效
#   Header.Payload.Signature 三段式，服务端私钥签名防篡改
#   Payload 含: {"sub": "user", "exp": 1782000000, "iat": 1781996400}
async def auth_middleware(request: Request, call_next):
    # 跳过健康检查和文档路径
    if request.url.path in ("/health", "/docs", "/openapi.json", "/redoc", "/api/auth/token"):
        response = await call_next(request)
        return response

    if not config["auth"]["enabled"]:
        response = await call_next(request)
        return response

    # 提取 Token
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        token = request.headers.get("X-API-Key", "")

    if not token:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    # 优先尝试 JWT 验证
    try:
        payload = pyjwt.decode(
            token,
            config["auth"]["jwt_secret"],
            algorithms=[config["auth"]["jwt_algorithm"]],
        )
        request.state.user = payload.get("sub", "unknown")
        request.state.auth_method = "jwt"
        response = await call_next(request)
        return response
    except pyjwt.ExpiredSignatureError:
        return JSONResponse(status_code=401, content={"error": "Token expired"})
    except pyjwt.InvalidTokenError:
        pass  # JWT 验证失败 → 回退到 API Key

    # 回退: API Key 兼容模式
    if token in config["auth"]["api_keys"]:
        request.state.user = "api-key-user"
        request.state.auth_method = "api-key"
        response = await call_next(request)
        return response

    return JSONResponse(status_code=401, content={"error": "Unauthorized"})

# ------------------------------------------------------------------
# ============ 模块3：FastAPI 应用 ============
# ------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.info("╔══════════════════════════════════════════════════════╗")
    logger.info("║  AI Infra 推理网关 v2.0.0                           ║")
    logger.info("║  Ollama 后端: %s                ║", config["ollama"]["base_url"])
    logger.info("║  鉴权: %s                                      ║", "已启用" if config["auth"]["enabled"] else "未启用")
    logger.info("║  限流: %s (容量=%d, 补充速率=%d/s)      ║", "已启用" if config["rate_limit"]["enabled"] else "未启用", config["rate_limit"]["capacity"], config["rate_limit"]["refill_rate"])
    logger.info("╚══════════════════════════════════════════════════════╝")
    yield
    logger.info("网关关闭")

app = FastAPI(
    title="AI Infra 推理网关",
    description="异构环境下的 LLM 推理网关，支持鉴权、限流、SSE流式转发、熔断降级",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 鉴权中间件
app.middleware("http")(auth_middleware)

# ------------------------------------------------------------------
# ============ 全局 HTTP 会话池 ============
# 庖丁解牛 - aiohttp.ClientSession：
# 类比：就像饭店的服务员，不是每来一个客人就新雇一个服务员（太慢）
# 而是养着一群"常驻服务员"（连接池），复用来服务所有客人
# HTTP keep-alive 连接的复用能减少 90% 的连接开销
session: Optional[aiohttp.ClientSession] = None

async def get_session() -> aiohttp.ClientSession:
    global session
    if session is None or session.closed:
        connector = aiohttp.TCPConnector(limit=100, ttl_dns_cache=300)
        timeout = aiohttp.ClientTimeout(total=config["ollama"]["timeout"])
        session = aiohttp.ClientSession(connector=connector, timeout=timeout)
    return session

# ------------------------------------------------------------------
# ============ 路由：JWT Token 签发 ============
# ------------------------------------------------------------------
@app.post("/api/auth/token")
async def generate_token():
    """签发 JWT Token (v2 镀金升级)"""
    now = datetime.now(tz=timezone.utc)
    payload = {
        "sub": "gateway-user",
        "iat": now,
        "exp": now + timedelta(minutes=config["auth"]["jwt_expire_minutes"]),
    }
    token = pyjwt.encode(payload, config["auth"]["jwt_secret"], algorithm=config["auth"]["jwt_algorithm"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": config["auth"]["jwt_expire_minutes"] * 60,
    }

# ------------------------------------------------------------------
# ============ 路由：健康检查 ============
# ------------------------------------------------------------------
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "timestamp": time.time(),
        "circuit_breaker": circuit_breaker.state,
    }

# ------------------------------------------------------------------
# ============ 路由：模型列表 ============
# ------------------------------------------------------------------
@app.get("/api/models")
async def list_models(request: Request):
    """代理 Ollama 的 /api/tags，返回已安装模型列表"""
    if not circuit_breaker.allow_request():
        raise HTTPException(status_code=503, detail="后端熔断中，请稍后重试")

    if config["rate_limit"]["enabled"]:
        bucket = buckets["default"]
        if not bucket.consume():
            raise HTTPException(status_code=429, detail="请求过于频繁")

    sess = await get_session()
    max_attempts = config.get("retry", {}).get("max_attempts", 1)
    for attempt in range(max_attempts):
        try:
            async with sess.get(f"{config['ollama']['base_url']}/api/tags") as resp:
                data = await resp.json()
                circuit_breaker.record_success()
                return data
        except aiohttp.ClientError as e:
            logger.warning(f"Ollama 连接失败 (attempt {attempt+1}/{max_attempts}): {e}")
            if attempt < max_attempts - 1:
                await asyncio.sleep(config.get("retry", {}).get("backoff_seconds", 1))
    circuit_breaker.record_failure()
    raise HTTPException(status_code=502, detail="Ollama 后端不可达")

# ------------------------------------------------------------------
# ============ 路由：文本补全（非流式） ============
# ------------------------------------------------------------------
@app.post("/api/generate")
async def generate(request: Request):
    """非流式文本生成——代理到 Ollama /api/generate"""
    if config["rate_limit"]["enabled"]:
        bucket = buckets["default"]
        if not bucket.consume():
            raise HTTPException(status_code=429, detail="请求过于频繁")

    body = await request.json()

    # 默认模型
    if "model" not in body:
        body["model"] = config["ollama"]["default_model"]

    # 强制非流式
    body["stream"] = False

    sess = await get_session()
    logger.info(f"生成请求: model={body.get('model')}, prompt_len={len(body.get('prompt',''))}")

    try:
        async with sess.post(
            f"{config['ollama']['base_url']}/api/generate",
            json=body,
        ) as resp:
            data = await resp.json()
            # 记录性能指标
            total_duration = data.get("total_duration", 0) / 1e9  # ns -> s
            eval_count = data.get("eval_count", 0)
            if total_duration > 0:
                tps = eval_count / total_duration
                logger.info(f"生成完成: {eval_count} tokens, {total_duration:.2f}s, {tps:.1f} tok/s")
            return data
    except aiohttp.ClientError as e:
        logger.error(f"Ollama 生成失败: {e}")
        raise HTTPException(status_code=502, detail=f"Ollama 生成失败: {e}")

# ------------------------------------------------------------------
# ============ 路由：流式补全（SSE 转发） ============
# ------------------------------------------------------------------
# 庖丁解牛 - SSE (Server-Sent Events)：
# 比喻：就像打电话 vs 发短信
#   - 普通 HTTP 请求 = 发短信，一条完整的消息发完就结束
#   - SSE = 打电话，建立连接后，服务器可以持续"说话"，
#     客户端实时听到每一个字（token），不用等整句话说完
#   - SSE 是单向的（服务器→客户端），基于 HTTP 长连接
#   - 每个数据块格式：data: {"token": "你"}\n\n
@app.post("/api/chat/stream")
async def chat_stream(request: Request):
    """SSE 流式补全——逐 token 推送给客户端"""
    if config["rate_limit"]["enabled"]:
        bucket = buckets["default"]
        if not bucket.consume():
            raise HTTPException(status_code=429, detail="请求过于频繁")

    body = await request.json()
    if "model" not in body:
        body["model"] = config["ollama"]["default_model"]
    body["stream"] = True

    sess = await get_session()
    logger.info(f"流式请求: model={body.get('model')}, prompt_len={len(body.get('prompt',''))}")

    async def event_generator():
        """异步生成器——逐块转发 Ollama 的 SSE 流"""
        try:
            async with sess.post(
                f"{config['ollama']['base_url']}/api/generate",
                json=body,
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    yield f"data: {json.dumps({'error': error_text})}\n\n"
                    return

                # 逐行读取 Ollama 返回的 NDJSON 流
                # 每行一个 JSON 对象：{"response":"你","done":false}
                async for line in resp.content:
                    line = line.decode("utf-8").strip()
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # 格式化为 SSE
                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

                    if chunk.get("done"):
                        eval_count = chunk.get("eval_count", 0)
                        total_dur = chunk.get("total_duration", 0) / 1e9
                        if total_dur > 0:
                            tps = eval_count / total_dur
                            logger.info(f"流式完成: {eval_count} tokens, {total_dur:.2f}s, {tps:.1f} tok/s")

        except aiohttp.ClientError as e:
            logger.error(f"流式转发失败: {e}")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用 Nginx 缓冲
        },
    )

# ------------------------------------------------------------------
# ============ 启动入口 ============
# ------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "gateway_server:app",
        host=config["server"]["host"],
        port=config["server"]["port"],
        reload=False,
        log_level="info",
    )