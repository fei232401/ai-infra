"""
Unified API Gateway — FastAPI server that integrates the Scheduler with:
  - Auth middleware (Bearer Token)
  - Rate limiting (Token Bucket, global + per-backend)
  - Intelligent routing (model-aware, latency, throughput, cost)
  - SSE streaming proxy
  - Status & metrics endpoints

Run: python unified_gateway.py
"""

import asyncio
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from typing import Optional

import aiohttp
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

# ---------------------------------------------------------------------------
# OpenTelemetry Distributed Tracing (TEMPO-ready) — v1.1
# ---------------------------------------------------------------------------
try:
    if os.environ.get("OTEL_ENABLED") == "1":
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
        from opentelemetry.sdk.resources import Resource

        resource = Resource.create({"service.name": "ai-model-scheduler", "service.version": "1.1.0"})
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer("scheduler")
        HAS_OTEL = True
        logging.getLogger("unified_gateway").info("[OTEL] Tracing enabled")
    else:
        HAS_OTEL = False
except ImportError:
    HAS_OTEL = False
except Exception:
    HAS_OTEL = False

# Add parent directory and 01-scheduler-core to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "01-scheduler-core"))

from backend_registry import load_config
from middleware.auth import AuthMiddleware
from middleware.rate_limit import PerBackendRateLimiter, TokenBucket, create_rate_limit_middleware
from scheduler import Scheduler

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] UnifiedGateway: %(message)s",
)
logger = logging.getLogger("unified_gateway")

# ---------------------------------------------------------------------------
# Config & State
# ---------------------------------------------------------------------------

CONFIG_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "01-scheduler-core",
    "config",
    "scheduler_config.yaml",
)

config = load_config(CONFIG_PATH)
PORT = config.get("scheduler", {}).get("port", 9000)

# Rate limiter (used at handler level)
_, backend_rate_limiter = create_rate_limit_middleware(config)

# Global scheduler instance — initialized in lifespan
scheduler: Optional[Scheduler] = None


# ---------------------------------------------------------------------------
# App Lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup & shutdown hooks."""
    global scheduler
    logger.info("=" * 60)
    logger.info(f"AI Model Scheduler starting on port {PORT}")
    logger.info("=" * 60)
    scheduler = Scheduler(config)
    await scheduler.start()
    logger.info(f"Registered backends: {list(scheduler.registry.backends.keys())}")
    yield
    logger.info("Shutting down scheduler...")
    if scheduler:
        await scheduler.stop()
    logger.info("Scheduler stopped.")


# ---------------------------------------------------------------------------
# Create FastAPI App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="AI Model Scheduler",
    description="Unified inference gateway with intelligent routing across heterogeneous backends",
    version="1.0.0",
    lifespan=lifespan,
)

# Apply auth middleware only (rate limit is applied at handler level)
api_keys = config.get("auth", {}).get("api_keys", [])
auth_enabled = config.get("auth", {}).get("enabled", True)
app.add_middleware(AuthMiddleware, api_keys=api_keys, enabled=auth_enabled)


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health_endpoint():
    """Health check endpoint (public)."""
    uptime = 0.0
    if scheduler and scheduler.start_time:
        uptime = round(time.time() - scheduler.start_time, 1)
    return {
        "status": "healthy",
        "service": "ai-model-scheduler",
        "version": "1.0.0",
        "uptime_seconds": uptime,
    }


@app.get("/v1/status")
async def get_status():
    """Get full scheduler status including all backends."""
    if not scheduler:
        return JSONResponse({"error": "Scheduler not initialized"}, status_code=503)
    return JSONResponse(scheduler.get_status())


@app.get("/v1/metrics")
async def get_metrics():
    """Get Prometheus-compatible metrics snapshot."""
    if not scheduler:
        return JSONResponse({"error": "Scheduler not initialized"}, status_code=503)
    return JSONResponse(scheduler.get_metrics())


@app.get("/v1/backends")
async def list_backends():
    """List all registered backends with their status."""
    if not scheduler:
        return JSONResponse({"error": "Scheduler not initialized"}, status_code=503)
    return JSONResponse(scheduler.registry.get_all_status())


# ---------------------------------------------------------------------------
# Inference Proxy (Core Feature)
# ---------------------------------------------------------------------------

@app.post("/v1/chat/completions")
@app.post("/api/chat")
async def chat_completions(request: Request):
    """
    OpenAI-compatible chat completions endpoint.

    Routes to the best backend based on model + strategy.
    Supports both streaming (SSE) and non-streaming modes.

    Headers:
        Authorization: Bearer <api-key>
        X-Routing-Strategy: latency | throughput | cost  (optional)
        X-Session-Id: <uuid>  (optional, for session affinity)
    """
    if not scheduler:
        raise HTTPException(status_code=503, detail="Scheduler not initialized")

    # Parse request body
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    model = body.get("model", "qwen2.5:0.5b")
    stream = body.get("stream", False)
    strategy = request.headers.get("X-Routing-Strategy")
    session_id = request.headers.get("X-Session-Id")

    # --- Route request ---
    backend, routing_info = scheduler.route_request(
        model=model,
        strategy=strategy,
        session_id=session_id,
    )

    if backend is None:
        logger.warning(f"No backend available for model '{model}'")
        raise HTTPException(
            status_code=503,
            detail={
                "error": "Service Unavailable",
                "detail": f"No healthy backend available for model '{model}'",
                "routing_info": routing_info,
            },
        )

    logger.info(
        f"Routed '{model}' → {backend.id} ({backend.engine}) "
        f"[strategy={routing_info['strategy']}, "
        f"ttft={routing_info['score']['ttft_ms']}ms, "
        f"tps={routing_info['score']['throughput_tps']}]"
    )

    # --- Per-backend rate limit check ---
    if not await backend_rate_limiter.consume(backend.id):
        scheduler.release_backend(backend.id)
        raise HTTPException(
            status_code=429,
            detail={
                "error": "Too Many Requests",
                "detail": f"Backend '{backend.id}' rate limit exceeded.",
            },
        )

    # --- Forward request to backend ---
    backend_url = f"{backend.url}{backend.chat_path}"
    start_time = time.time()

    try:
        sess = await scheduler.get_http_session()
        if stream:
            # Streaming SSE proxy
            async with sess.post(backend_url, json=body) as resp:
                if resp.status >= 400:
                    error_text = await resp.text()
                    scheduler.record_result(backend.id, success=False)
                    raise HTTPException(
                        status_code=resp.status,
                        detail=f"Backend error: {error_text[:200]}",
                    )

                first_token_time = None
                token_count = 0

                async def stream_proxy():
                    nonlocal first_token_time, token_count
                    async for line in resp.content:
                        decoded = line.decode("utf-8", errors="replace")
                        if decoded.startswith("data: "):
                            decoded = decoded[6:]
                        if first_token_time is None:
                            first_token_time = time.time()
                        token_count += 1
                        yield decoded

                response = StreamingResponse(
                    stream_proxy(),
                    media_type="text/event-stream",
                )

                # Record streaming metrics with real data
                if first_token_time is not None and token_count > 0:
                    ttft_ms = (first_token_time - start_time) * 1000
                    elapsed_sec = max(time.time() - start_time, 0.001)
                    tps = token_count / elapsed_sec
                    scheduler.record_result(
                        backend.id, success=True,
                        ttft_ms=ttft_ms, tps=tps, tokens=token_count
                    )
                else:
                    scheduler.record_result(
                        backend.id, success=True, ttft_ms=50, tps=10, tokens=20
                    )

                response.headers["X-Routed-Backend"] = backend.id
                response.headers["X-Routing-Strategy"] = routing_info["strategy"]
                return response

        else:
            # Non-streaming proxy
            async with sess.post(backend_url, json=body) as resp:
                response_data = await resp.json()
                elapsed_ms = (time.time() - start_time) * 1000

                if resp.status >= 400:
                    scheduler.record_result(backend.id, success=False)
                    raise HTTPException(
                        status_code=resp.status,
                        detail=response_data,
                    )

                # Estimate metrics from response
                ttft_ms = (
                    response_data.get("total_duration", elapsed_ms) / 1000
                    if "total_duration" in response_data
                    else elapsed_ms
                )
                token_count = response_data.get(
                    "eval_count",
                    response_data.get("usage", {}).get("completion_tokens", 20),
                )
                total_time_s = max(elapsed_ms / 1000, 0.001)
                tps = token_count / total_time_s

                scheduler.record_result(
                    backend.id,
                    success=True,
                    ttft_ms=ttft_ms,
                    tps=tps,
                    tokens=token_count,
                )

                response_data["_routing"] = routing_info

                response = JSONResponse(response_data)
                response.headers["X-Routed-Backend"] = backend.id
                response.headers["X-Routing-Strategy"] = routing_info["strategy"]
                return response

    except HTTPException:
        raise
    except asyncio.TimeoutError:
        scheduler.record_result(backend.id, success=False)
        raise HTTPException(
            status_code=504,
            detail={
                "error": "Gateway Timeout",
                "detail": f"Backend '{backend.id}' did not respond in time.",
            },
        )
    except aiohttp.ClientError as e:
        scheduler.record_result(backend.id, success=False)
        raise HTTPException(
            status_code=502,
            detail={
                "error": "Bad Gateway",
                "detail": f"Backend '{backend.id}' connection error: {str(e)}",
            },
        )
    except Exception as e:
        scheduler.record_result(backend.id, success=False)
        logger.exception(f"Unexpected error proxying to {backend.id}")
        raise HTTPException(
            status_code=500,
            detail={"error": "Internal Server Error", "detail": str(e)},
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("  AI Model Scheduler — Unified Inference Gateway")
    logger.info(f"  Port: {PORT}")
    logger.info(f"  Swagger: http://localhost:{PORT}/docs")
    logger.info(f"  Status:  http://localhost:{PORT}/v1/status")
    logger.info("=" * 60)
    logger.info("Backend pool (from config):")
    for b in config.get("backends", []):
        logger.info(
            f"  - {b['id']}: {b['url']} [{', '.join(b.get('models', []))}]"
        )
    logger.info("=" * 60)
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")