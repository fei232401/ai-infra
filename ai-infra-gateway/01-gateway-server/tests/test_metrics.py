"""
/metrics 端点及 Prometheus 指标采集测试
新增 7 个用例
"""
import pytest


class TestMetricsEndpoint:

    def test_accessible_without_auth(self, client):
        """/metrics 在鉴权白名单中，无需 Token"""
        resp = client.get("/metrics")
        assert resp.status_code == 200

    def test_content_type_is_prometheus(self, client):
        resp = client.get("/metrics")
        assert "text/plain" in resp.headers["content-type"]

    def test_contains_all_metric_names(self, client):
        resp = client.get("/metrics")
        body = resp.text
        for name in [
            "gateway_requests_total",
            "gateway_request_duration_seconds",
            "gateway_requests_in_progress",
            "gateway_circuit_breaker_state",
            "gateway_ollama_requests_total",
        ]:
            assert name in body, f"缺少指标: {name}"


class TestMetricsCollection:

    def test_request_counted_with_path_label(self, client, auth_headers, mock_ollama):
        client.get("/api/models", headers=auth_headers)
        resp = client.get("/metrics")
        assert 'path="/api/models"' in resp.text

    def test_unauthorized_request_produces_401(self, client):
        client.get("/api/models")
        resp = client.get("/metrics")
        assert 'status_code="401"' in resp.text

    def test_circuit_breaker_default_closed(self, client):
        resp = client.get("/metrics")
        assert "gateway_circuit_breaker_state 0.0" in resp.text

    def test_health_request_is_counted(self, client):
        client.get("/health")
        resp = client.get("/metrics")
        assert 'path="/health"' in resp.text
