"""路由端点集成测试

测试对象：gateway_server 的所有 HTTP 路由
使用 mock_ollama fixture 隔离 Ollama 后端依赖
"""

import json
import pytest
import aiohttp
import gateway_server


class TestHealthRoute:

    def test_health_returns_ok(self, client):
        """GET /health 返回状态信息"""
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "circuit_breaker" in data
        assert "timestamp" in data


class TestModelsRoute:

    def test_list_models_success(self, client, auth_headers, mock_ollama):
        """正常情况返回模型列表"""
        resp = client.get("/api/models", headers=auth_headers)
        assert resp.status_code == 200
        assert "models" in resp.json()

    def test_ollama_down_returns_502(self, client, auth_headers, mock_ollama, no_retry):
        """Ollama 不可达时返回 502"""
        mock_ollama.get.side_effect = aiohttp.ClientError("Connection refused")
        resp = client.get("/api/models", headers=auth_headers)
        assert resp.status_code == 502


class TestGenerateRoute:

    def test_generate_success(self, client, auth_headers, mock_ollama):
        """非流式生成返回完整结果"""
        resp = client.post(
            "/api/generate",
            json={"prompt": "Hello"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "response" in data
        assert data["done"] is True


class TestStreamRoute:

    def test_chat_stream_returns_sse(self, client, auth_headers, mock_ollama_stream):
        """流式接口返回 SSE 格式数据"""
        resp = client.post(
            "/api/chat/stream",
            json={"prompt": "Hello"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        # 验证 SSE 数据格式
        lines = [l for l in resp.text.strip().split("\n") if l.startswith("data:")]
        assert len(lines) == 2  # 2 个 chunk


class TestRateLimit:

    def test_rate_limit_kicks_in(self, client, auth_headers, mock_ollama):
        """超过令牌桶容量后返回 429"""
        capacity = gateway_server.config["rate_limit"]["capacity"]
        success_count = 0
        rate_limited = False

        for _ in range(capacity + 10):
            resp = client.get("/api/models", headers=auth_headers)
            if resp.status_code == 200:
                success_count += 1
            elif resp.status_code == 429:
                rate_limited = True
                break

        assert rate_limited, "应该触发限流（429）"
        assert success_count <= capacity


class TestCircuitBreakerIntegration:

    def test_circuit_breaker_opens_after_failures(self, client, auth_headers, mock_ollama, no_retry):
        """连续失败达到阈值后，熔断器打开，返回 503"""
        # 让 Ollama 模拟不可达
        mock_ollama.get.side_effect = aiohttp.ClientError("Connection refused")

        threshold = gateway_server.config["circuit_breaker"]["failure_threshold"]

        # 前 N 次请求触发 502（后端不可达）+ 记录失败
        for _ in range(threshold):
            resp = client.get("/api/models", headers=auth_headers)
            assert resp.status_code == 502

        # 第 N+1 次请求被熔断器拦截
        resp = client.get("/api/models", headers=auth_headers)
        assert resp.status_code == 503
        assert "熔断" in resp.json()["detail"]
