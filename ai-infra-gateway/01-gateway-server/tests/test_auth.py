"""JWT + API Key 双模鉴权中间件测试

测试对象：gateway_server.auth_middleware
覆盖场景：JWT 有效/过期/无效、API Key 兼容、无 Token、白名单路径
"""

import pytest
import jwt as pyjwt
from datetime import datetime, timedelta
import gateway_server


class TestAuthBypass:
    """白名单路径跳过鉴权"""

    def test_health_bypasses_auth(self, client):
        """健康检查不需要认证"""
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_docs_bypasses_auth(self, client):
        """API 文档路径不需要认证"""
        resp = client.get("/docs")
        assert resp.status_code == 200

    def test_generate_token_bypasses_auth(self, client):
        """签发 Token 接口不需要认证"""
        resp = client.post("/api/auth/token")
        assert resp.status_code == 200


class TestJWTAuth:
    """JWT Token 鉴权"""

    def test_valid_jwt_accepted(self, client, auth_headers, mock_ollama):
        """有效 JWT 可以访问受保护接口"""
        resp = client.get("/api/models", headers=auth_headers)
        assert resp.status_code == 200

    def test_expired_jwt_rejected(self, client, expired_jwt_token):
        """过期 JWT 返回 401 + Token expired"""
        headers = {"Authorization": f"Bearer {expired_jwt_token}"}
        resp = client.get("/api/models", headers=headers)
        assert resp.status_code == 401
        assert "expired" in resp.json()["error"].lower()

    def test_invalid_jwt_rejected(self, client):
        """无效 JWT（签名错误/格式错误）返回 401"""
        headers = {"Authorization": "Bearer totally-invalid-garbage-token"}
        resp = client.get("/api/models", headers=headers)
        assert resp.status_code == 401


class TestAPIKeyAuth:
    """API Key 兼容模式"""

    def test_valid_api_key_accepted(self, client, api_key_headers, mock_ollama):
        """有效 API Key 可以访问受保护接口"""
        resp = client.get("/api/models", headers=api_key_headers)
        assert resp.status_code == 200


class TestNoAuth:
    """无认证信息"""

    def test_no_token_rejected(self, client):
        """不携带任何 Token 返回 401"""
        resp = client.get("/api/models")
        assert resp.status_code == 401


class TestTokenGeneration:
    """JWT Token 签发接口"""

    def test_generate_token_returns_valid_jwt(self, client):
        """签发接口返回有效 JWT"""
        resp = client.post("/api/auth/token")
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["expires_in"] > 0

        # 验证签发的 Token 可以通过鉴权
        token = data["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        # 用签发的 Token 访问受保护接口（需要 mock Ollama）
        # 这里只验证 Token 格式合法
        cfg = gateway_server.config
        payload = pyjwt.decode(token, cfg["auth"]["jwt_secret"], algorithms=[cfg["auth"]["jwt_algorithm"]])
        assert payload["sub"] == "gateway-user"
