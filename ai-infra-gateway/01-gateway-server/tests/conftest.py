"""
Gateway 测试公共 Fixtures
- 全局状态自动重置（autouse）
- 认证 Token 生成
- Ollama 后端 Mock
"""
import sys
import os

# 将 gateway_server.py 所在目录加入 Python 路径
GATEWAY_DIR = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.abspath(GATEWAY_DIR))

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
import aiohttp
import gateway_server
from gateway_server import CircuitBreaker


# ============================================================
# 辅助函数
# ============================================================
def _make_async_cm(response_obj):
    """创建异步上下文管理器 Mock（模拟 async with session.get/post(...)）"""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=response_obj)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


# ============================================================
# 全局状态自动重置（每个测试前后执行）
# ============================================================
@pytest.fixture(autouse=True)
def _reset_global_state():
    """重置模块级全局变量，确保测试之间互相隔离"""
    gateway_server.buckets.clear()
    cb = gateway_server.circuit_breaker
    cb.state = cb.CLOSED
    cb.failure_count = 0
    cb.last_failure_time = 0
    gateway_server.session = None
    yield
    gateway_server.buckets.clear()
    gateway_server.session = None


# ============================================================
# App & Client
# ============================================================
@pytest.fixture
def app():
    return gateway_server.app


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient
    return TestClient(app)


# ============================================================
# 认证 Fixtures
# ============================================================
@pytest.fixture
def valid_jwt_token():
    """生成一个有效期 30 分钟的 JWT Token"""
    import jwt as pyjwt
    from datetime import datetime, timedelta, timezone
    cfg = gateway_server.config
    payload = {
        "sub": "test-user",
        "iat": datetime.now(tz=timezone.utc),
        "exp": datetime.now(tz=timezone.utc) + timedelta(minutes=30),
    }
    return pyjwt.encode(payload, cfg["auth"]["jwt_secret"], algorithm=cfg["auth"]["jwt_algorithm"])


@pytest.fixture
def expired_jwt_token():
    """生成一个已过期的 JWT Token"""
    import jwt as pyjwt
    from datetime import datetime, timedelta, timezone
    cfg = gateway_server.config
    payload = {
        "sub": "test-user",
        "iat": datetime.now(tz=timezone.utc) - timedelta(hours=2),
        "exp": datetime.now(tz=timezone.utc) - timedelta(hours=1),
    }
    return pyjwt.encode(payload, cfg["auth"]["jwt_secret"], algorithm=cfg["auth"]["jwt_algorithm"])


@pytest.fixture
def valid_api_key():
    return gateway_server.config["auth"]["api_keys"][0]


@pytest.fixture
def auth_headers(valid_jwt_token):
    return {"Authorization": f"Bearer {valid_jwt_token}"}


@pytest.fixture
def api_key_headers(valid_api_key):
    return {"X-API-Key": valid_api_key}


@pytest.fixture
def no_retry():
    """禁用重试 + 去掉退避等待，加速测试"""
    original = gateway_server.config["retry"].copy()
    gateway_server.config["retry"]["max_attempts"] = 1
    gateway_server.config["retry"]["backoff_seconds"] = 0
    yield
    gateway_server.config["retry"] = original


# ============================================================
# Mock Ollama 后端
# ============================================================
@pytest.fixture
def mock_ollama():
    """Mock Ollama HTTP 会话，默认返回成功响应"""
    mock_session = MagicMock()
    mock_session.closed = False

    # GET → 模型列表成功
    get_resp = AsyncMock()
    get_resp.status = 200
    get_resp.json = AsyncMock(return_value={"models": [{"name": "qwen2.5:1.5b"}]})
    mock_session.get.return_value = _make_async_cm(get_resp)

    # POST → 文本生成成功
    post_resp = AsyncMock()
    post_resp.status = 200
    post_resp.json = AsyncMock(return_value={
        "response": "Hello world",
        "done": True,
        "total_duration": 1_000_000_000,   # 1秒（纳秒单位）
        "eval_count": 10,
    })
    mock_session.post.return_value = _make_async_cm(post_resp)

    with patch("gateway_server.get_session", new=AsyncMock(return_value=mock_session)):
        yield mock_session


# ============================================================
# Mock Ollama 流式响应
# ============================================================
class MockStreamContent:
    """模拟 aiohttp.StreamReader 的异步迭代协议"""

    def __init__(self, lines: list):
        self._lines = lines
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._lines):
            raise StopAsyncIteration
        item = self._lines[self._index]
        self._index += 1
        return item


@pytest.fixture
def mock_ollama_stream(mock_ollama):
    """在 mock_ollama 基础上，将 POST 响应替换为流式 NDJSON"""
    stream_resp = AsyncMock()
    stream_resp.status = 200
    stream_resp.content = MockStreamContent([
        b'{"response":"Hello","done":false}',
        b'{"response":" world","done":true,"total_duration":1000000000,"eval_count":2}',
    ])
    mock_ollama.post.return_value = _make_async_cm(stream_resp)
    return mock_ollama
