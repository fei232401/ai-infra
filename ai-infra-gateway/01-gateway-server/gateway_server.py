#!/usr/bin/env python3
"""AI Infra Gateway v11.0 — 双后端路由: CPU Ollama + GPU vLLM (chat completions fix)"""
import asyncio, time, json, logging, os
from typing import Optional
from contextlib import asynccontextmanager
from collections import defaultdict
import yaml, aiohttp, jwt as pyjwt
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.responses import StreamingResponse, JSONResponse
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("gateway")

def load_config(path=None):
    if path is None:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(base_dir, "config", "gateway_config.yaml")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

config = load_config()

def get_backend(model_name: str) -> tuple:
    for _bid, bconf in config.get("backends", {}).items():
        if model_name in bconf.get("models", []):
            return bconf["base_url"], bconf["type"]
    return config["ollama"]["base_url"], "ollama"

# ---------- helper: 统一构造 chat messages ----------
def _to_messages(body: dict) -> list:
    """将任何请求格式统一转换为 messages 列表"""
    if "messages" in body:
        return body["messages"]
    prompt = body.get("prompt", "")
    return [{"role": "user", "content": prompt}]

# ---------- Prometheus ----------
REQUEST_COUNT = Counter('gateway_requests_total', 'Total requests', ['method', 'path', 'status_code'])
REQUEST_LATENCY = Histogram('gateway_request_duration_seconds', 'Request latency', ['method', 'path'],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0])
IN_PROGRESS = Gauge('gateway_requests_in_progress', 'In-progress', ['method', 'path'])
CIRCUIT_STATE = Gauge('gateway_circuit_breaker_state', 'Circuit breaker state')
OLLAMA_REQUESTS = Counter('gateway_ollama_requests_total', 'Ollama backend', ['endpoint', 'status_code'])
VLLM_REQUESTS = Counter('gateway_vllm_requests_total', 'vLLM backend', ['endpoint', 'status_code'])

# ---------- Rate Limiting ----------
class TokenBucket:
    def __init__(self, capacity, refill_rate):
        self.capacity, self.refill_rate = capacity, refill_rate
        self.tokens, self.last_refill = capacity, time.monotonic()
    def consume(self, tokens=1):
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.last_refill) * self.refill_rate)
        self.last_refill = now
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False

buckets = defaultdict(lambda: TokenBucket(config["rate_limit"]["capacity"], config["rate_limit"]["refill_rate"]))

# ---------- Circuit Breaker ----------
class CircuitBreaker:
    CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"
    def __init__(self, ft, ts):
        self.failure_threshold, self.timeout_seconds = ft, ts
        self.state, self.failure_count, self.last_failure_time = self.CLOSED, 0, 0
    def record_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.monotonic()
        if self.failure_count >= self.failure_threshold:
            self.state = self.OPEN
    def record_success(self):
        self.failure_count, self.state = 0, self.CLOSED
    def allow_request(self):
        if self.state == self.CLOSED: return True
        if self.state == self.OPEN:
            if time.monotonic() - self.last_failure_time > self.timeout_seconds:
                self.state = self.HALF_OPEN
                return True
            return False
        return True

circuit_breaker = CircuitBreaker(config["circuit_breaker"]["failure_threshold"], config["circuit_breaker"]["timeout_seconds"])

# ---------- Auth ----------
async def auth_middleware(request: Request, call_next):
    if request.url.path in ("/health", "/docs", "/openapi.json", "/redoc", "/api/auth/token", "/metrics"):
        return await call_next(request)
    if not config["auth"]["enabled"]:
        return await call_next(request)
    token = request.headers.get("Authorization", "").replace("Bearer ", "") or request.headers.get("X-API-Key", "")
    if not token:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    try:
        pyjwt.decode(token, config["auth"]["jwt_secret"], algorithms=[config["auth"]["jwt_algorithm"]])
        return await call_next(request)
    except pyjwt.InvalidTokenError:
        pass
    if token in config["auth"]["api_keys"]:
        return await call_next(request)
    return JSONResponse(status_code=401, content={"error": "Unauthorized"})

# ---------- Session ----------
session: Optional[aiohttp.ClientSession] = None

async def get_session() -> aiohttp.ClientSession:
    global session
    if session is None or session.closed:
        session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=100, ttl_dns_cache=300),
                                         timeout=aiohttp.ClientTimeout(total=config["ollama"]["timeout"]))
    return session

# ---------- App ----------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 50)
    logger.info("AI Infra Gateway v11.0")
    logger.info(f"Backends: {list(config.get('backends', {}).keys())}")
    logger.info("=" * 50)
    yield
    if session and not session.closed:
        await session.close()

app = FastAPI(title="AI Infra Gateway", version="11.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ---------- Middleware ----------
@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    if request.url.path == "/metrics":
        return await call_next(request)
    IN_PROGRESS.labels(method=request.method, path=request.url.path).inc()
    start = time.monotonic()
    try:
        response = await call_next(request)
        REQUEST_COUNT.labels(method=request.method, path=request.url.path, status_code=str(response.status_code)).inc()
        REQUEST_LATENCY.labels(method=request.method, path=request.url.path).observe(time.monotonic() - start)
        CIRCUIT_STATE.set({"closed": 0, "open": 1, "half_open": 2}.get(circuit_breaker.state, -1))
        return response
    except Exception:
        REQUEST_COUNT.labels(method=request.method, path=request.url.path, status_code="500").inc()
        raise
    finally:
        IN_PROGRESS.labels(method=request.method, path=request.url.path).dec()

app.middleware("http")(auth_middleware)

# ---------- Endpoints ----------
@app.post("/api/auth/token")
async def generate_token():
    now = datetime.now(tz=timezone.utc)
    payload = {"sub": "gateway-user", "iat": now, "exp": now + timedelta(minutes=config["auth"]["jwt_expire_minutes"])}
    return {"access_token": pyjwt.encode(payload, config["auth"]["jwt_secret"], algorithm=config["auth"]["jwt_algorithm"]),
            "token_type": "bearer", "expires_in": config["auth"]["jwt_expire_minutes"] * 60}

@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": time.time(), "circuit_breaker": circuit_breaker.state, "version": "11.0"}

@app.get("/metrics")
async def metrics_endpoint():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.get("/api/models")
async def list_models(request: Request):
    if config["rate_limit"]["enabled"] and not buckets["default"].consume():
        raise HTTPException(status_code=429, detail="Rate limited")
    sess = await get_session()
    all_models = []
    try:
        async with sess.get(f"{config['ollama']['base_url']}/api/tags") as resp:
            OLLAMA_REQUESTS.labels(endpoint="/api/tags", status_code=str(resp.status)).inc()
            if resp.status == 200:
                data = await resp.json()
                for m in data.get("models", []):
                    m["backend"] = "ollama"
                all_models.extend(data.get("models", []))
    except aiohttp.ClientError as e:
        logger.warning(f"Ollama model list failed: {e}")
    for bid, bconf in config.get("backends", {}).items():
        if bconf["type"] == "vllm_openai":
            try:
                async with sess.get(f"{bconf['base_url']}/v1/models") as resp:
                    VLLM_REQUESTS.labels(endpoint="/v1/models", status_code=str(resp.status)).inc()
                    if resp.status == 200:
                        data = await resp.json()
                        for m in data.get("data", []):
                            all_models.append({"name": m["id"], "model": m["id"], "backend": "vllm"})
            except aiohttp.ClientError as e:
                logger.warning(f"vLLM model list failed: {e}")
    return {"models": all_models}

# ==================== /api/generate ====================
@app.post("/api/generate")
async def generate(request: Request):
    if config["rate_limit"]["enabled"] and not buckets["default"].consume():
        raise HTTPException(status_code=429, detail="Rate limited")
    body = await request.json()
    model = body.get("model", config["ollama"]["default_model"])
    base_url, backend_type = get_backend(model)
    body["stream"] = False
    sess = await get_session()
    logger.info(f"Generate: model={model}, backend={backend_type}")

    if backend_type == "vllm_openai":
        # 统一走 /v1/chat/completions，用 _to_messages 兼容 prompt 和 messages 两种格式
        vllm_body = {
            "model": model,
            "messages": _to_messages(body),
            "max_tokens": body.get("max_tokens", 512),
            "stream": False,
        }
        endpoint = f"{base_url}/v1/chat/completions"
        try:
            async with sess.post(endpoint, json=vllm_body) as resp:
                VLLM_REQUESTS.labels(endpoint="chat_completions", status_code=str(resp.status)).inc()
                data = await resp.json()
                if "choices" in data:
                    choice = data["choices"][0]
                    content = choice.get("message", {}).get("content", "")
                    return {"model": model, "response": content, "done": True, "backend": "vllm"}
                return data
        except aiohttp.ClientError as e:
            raise HTTPException(status_code=502, detail=f"vLLM error: {e}")
    else:
        try:
            async with sess.post(f"{base_url}/api/generate", json=body) as resp:
                OLLAMA_REQUESTS.labels(endpoint="/api/generate", status_code=str(resp.status)).inc()
                data = await resp.json()
                circuit_breaker.record_success()
                data["backend"] = "ollama"
                return data
        except aiohttp.ClientError as e:
            circuit_breaker.record_failure()
            raise HTTPException(status_code=502, detail=f"Ollama error: {e}")

# ==================== /api/chat/stream ====================
@app.post("/api/chat/stream")
async def chat_stream(request: Request):
    if config["rate_limit"]["enabled"] and not buckets["default"].consume():
        raise HTTPException(status_code=429, detail="Rate limited")
    body = await request.json()
    model = body.get("model", config["ollama"]["default_model"])
    base_url, backend_type = get_backend(model)
    is_chat = "messages" in body
    body["stream"] = True
    logger.info(f"Stream: model={model}, backend={backend_type}, is_chat={is_chat}")

    if backend_type == "vllm_openai":
        # 统一走 /v1/chat/completions
        vllm_body = {
            "model": model,
            "messages": _to_messages(body),
            "stream": True,
        }
        vllm_ep = "/v1/chat/completions"
        sess = await get_session()
        async def gen():
            try:
                async with sess.post(f"{base_url}{vllm_ep}", json=vllm_body) as resp:
                    VLLM_REQUESTS.labels(endpoint="chat_completions", status_code=str(resp.status)).inc()
                    if resp.status != 200:
                        yield f"data: {json.dumps({'error': await resp.text()})}\n\n"
                        return
                    tok_count = 0
                    async for raw in resp.content:
                        line = raw.decode("utf-8").strip()
                        if not line: continue
                        if line == "data: [DONE]":
                            yield f"data: {json.dumps({'model': model, 'response': '', 'done': True, 'backend': 'vllm', 'eval_count': tok_count})}\n\n"
                            continue
                        if line.startswith("data: "):
                            try: chunk = json.loads(line[6:])
                            except json.JSONDecodeError: continue
                            choices = chunk.get("choices", [])
                            if not choices: continue
                            delta = choices[0].get("delta", {})
                            text = delta.get("content", "")
                            finish = choices[0].get("finish_reason")
                            if finish == "stop":
                                yield f"data: {json.dumps({'model': model, 'response': '', 'done': True, 'backend': 'vllm', 'eval_count': tok_count})}\n\n"
                            elif text:
                                tok_count += 1
                                yield f"data: {json.dumps({'model': model, 'response': text, 'done': False, 'backend': 'vllm'}, ensure_ascii=False)}\n\n"
            except aiohttp.ClientError as e:
                VLLM_REQUESTS.labels(endpoint="chat_completions", status_code="error").inc()
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})
    else:
        ollama_ep = "/api/chat" if is_chat else "/api/generate"
        sess = await get_session()
        async def gen():
            try:
                async with sess.post(f"{base_url}{ollama_ep}", json=body) as resp:
                    OLLAMA_REQUESTS.labels(endpoint=ollama_ep, status_code=str(resp.status)).inc()
                    if resp.status != 200:
                        yield f"data: {json.dumps({'error': await resp.text()})}\n\n"
                        return
                    async for raw in resp.content:
                        line = raw.decode("utf-8").strip()
                        if not line: continue
                        try: chunk = json.loads(line)
                        except json.JSONDecodeError: continue
                        if is_chat and "message" in chunk:
                            chunk["response"] = chunk["message"].get("content", "")
                        chunk["backend"] = "ollama"
                        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            except aiohttp.ClientError as e:
                OLLAMA_REQUESTS.labels(endpoint=ollama_ep, status_code="error").inc()
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=config["server"]["host"], port=config["server"]["port"], reload=False, log_level="info")
