"""
Mock vLLM Backend — simulates a cloud GPU (RTX 4090) inference endpoint.

This is a standalone FastAPI server that:
  - Exposes OpenAI-compatible /api/chat and /api/generate endpoints
  - Simulates vLLM-class latency (TTFT ~50ms, high throughput)
  - Returns realistic-looking token streaming responses
  - Provides /health for scheduler health checks

Run: python mock_backend.py  (defaults to port 11436)
"""

import asyncio
import json
import logging
import random
import time
import uuid
from typing import AsyncGenerator

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] Mock-vLLM: %(message)s"
)
logger = logging.getLogger("mock_vllm")

app = FastAPI(title="Mock vLLM Backend", version="1.0.0")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PORT = 11436
TTFT_MIN_MS = 30         # minimum simulated TTFT
TTFT_MAX_MS = 80         # maximum simulated TTFT
TPOT_MS = 5              # simulated time per output token (ms)
MODEL_NAMES = ["qwen2.5:0.5b", "qwen2.5:1.5b"]

# Pre-canned responses for demo quality
MOCK_RESPONSES = {
    "zh": [
        "你好！我是一个AI助手，很高兴为你服务。",
        "这是一个很好的问题。让我来帮你分析一下...",
        "根据我的理解，这个问题可以从以下几个方面来看：",
        "当然可以！以下是我的分析和建议：",
    ],
    "en": [
        "Hello! I'm an AI assistant running on a simulated cloud GPU.",
        "That's an interesting question. Let me think about it...",
        "Here's my analysis based on the context provided:",
        "Great question! Here's what I can tell you:",
    ],
}

# Request counter for metrics
request_counter = 0
total_tokens_generated = 0

# ---------------------------------------------------------------------------
# Health & Info endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    """Scheduler health check endpoint."""
    return {
        "status": "healthy",
        "engine": "vllm-mock",
        "uptime_seconds": time.time() - START_TIME,
        "requests_served": request_counter,
        "tokens_generated": total_tokens_generated,
    }


@app.get("/api/tags")
async def list_models():
    """Ollama-compatible model listing."""
    return {
        "models": [
            {"name": m, "modified_at": "2026-01-01T00:00:00Z", "size": 500000000}
            for m in MODEL_NAMES
        ]
    }


# ---------------------------------------------------------------------------
# OpenAI-compatible Chat Completions
# ---------------------------------------------------------------------------

@app.post("/api/chat")
@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """Simulate an OpenAI-compatible chat completion with streaming."""
    global request_counter, total_tokens_generated
    request_counter += 1

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    model = body.get("model", MODEL_NAMES[0])
    messages = body.get("messages", [])
    stream = body.get("stream", False)

    # Simulate TTFT delay
    ttft_ms = random.uniform(TTFT_MIN_MS, TTFT_MAX_MS)
    await asyncio.sleep(ttft_ms / 1000.0)

    # Generate mock response text
    user_msg = messages[-1]["content"] if messages else "hello"
    response_text = _generate_mock_response(user_msg)
    token_count = len(response_text)

    total_tokens_generated += token_count

    if stream:
        return StreamingResponse(
            _stream_tokens(model, response_text, ttft_ms),
            media_type="text/event-stream"
        )
    else:
        return JSONResponse({
            "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": response_text,
                },
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": len(user_msg),
                "completion_tokens": token_count,
                "total_tokens": len(user_msg) + token_count,
            },
        })


async def _stream_tokens(model: str, text: str, ttft_ms: float) -> AsyncGenerator[str, None]:
    """SSE stream per-token chunks (Ollama format)."""
    chat_id = uuid.uuid4().hex[:8]
    tokens = list(text)  # character-by-character for simplicity
    total_tokens = len(tokens)

    for i, char in enumerate(tokens):
        chunk = {
            "model": model,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "message": {
                "role": "assistant",
                "content": char,
            },
            "done": (i == total_tokens - 1),
        }
        if chunk["done"]:
            chunk["total_duration"] = int(ttft_ms * 1_000_000)
            chunk["eval_count"] = total_tokens
            chunk["eval_duration"] = int(total_tokens * TPOT_MS * 1_000_000)
        yield json.dumps(chunk, ensure_ascii=False) + "\n"
        await asyncio.sleep(TPOT_MS / 1000.0)  # token-by-token delay


@app.post("/api/generate")
async def generate(request: Request):
    """Ollama-compatible generate endpoint (non-chat)."""
    global request_counter, total_tokens_generated
    request_counter += 1

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    model = body.get("model", MODEL_NAMES[0])
    prompt = body.get("prompt", "")
    stream = body.get("stream", False)

    ttft_ms = random.uniform(TTFT_MIN_MS, TTFT_MAX_MS)
    await asyncio.sleep(ttft_ms / 1000.0)

    response_text = _generate_mock_response(prompt)
    token_count = len(response_text)
    total_tokens_generated += token_count

    if stream:
        return StreamingResponse(
            _stream_generate(model, response_text, ttft_ms, token_count),
            media_type="text/event-stream"
        )
    else:
        return JSONResponse({
            "model": model,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "response": response_text,
            "done": True,
            "total_duration": int(ttft_ms * 1_000_000),
            "eval_count": token_count,
            "eval_duration": int(token_count * TPOT_MS * 1_000_000),
        })


async def _stream_generate(
    model: str, text: str, ttft_ms: float, token_count: int
) -> AsyncGenerator[str, None]:
    """SSE stream per-token chunks (Ollama generate format)."""
    for i, char in enumerate(text):
        chunk = {
            "model": model,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "response": char,
            "done": (i == len(text) - 1),
        }
        if chunk["done"]:
            chunk["total_duration"] = int(ttft_ms * 1_000_000)
            chunk["eval_count"] = token_count
            chunk["eval_duration"] = int(token_count * TPOT_MS * 1_000_000)
        yield json.dumps(chunk, ensure_ascii=False) + "\n"
        await asyncio.sleep(TPOT_MS / 1000.0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_mock_response(prompt: str) -> str:
    """Generate a simple mock response."""
    # Detect language
    has_chinese = any('\u4e00' <= c <= '\u9fff' for c in prompt)
    pool = MOCK_RESPONSES["zh"] if has_chinese else MOCK_RESPONSES["en"]
    base = random.choice(pool)

    # Add some context-dependent flavor
    if "?" in prompt or "？" in prompt:
        extra = "这是一个值得深入探讨的话题。" if has_chinese else " That's worth exploring further."
    else:
        extra = "还有其他问题吗？" if has_chinese else " Is there anything else I can help with?"
    return base + " " + extra


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

START_TIME = time.time()

if __name__ == "__main__":
    logger.info(f"Starting Mock vLLM Backend on port {PORT}")
    logger.info(f"Models: {MODEL_NAMES}")
    logger.info(f"Simulated TTFT range: {TTFT_MIN_MS}-{TTFT_MAX_MS}ms, TPOT: {TPOT_MS}ms")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")