"""FastAPI 路由测试。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client() -> TestClient:
    """创建 TestClient（模块级别复用，避免重复启动检测）。"""
    from app import app
    return TestClient(app)


class TestRootRoute:
    """首页路由测试。"""

    def test_index_returns_200(self, client: TestClient) -> None:
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]

    def test_index_contains_version(self, client: TestClient) -> None:
        r = client.get("/")
        assert "Video-Localizer" in r.text


class TestHealthRoute:
    """/api/health 路由测试。"""

    def test_health_returns_200(self, client: TestClient) -> None:
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/json"

    def test_health_has_required_keys(self, client: TestClient) -> None:
        r = client.get("/api/health")
        data = r.json()
        assert data["status"] == "ok"
        assert "version" in data
        assert "selected_profile" in data
        assert "asr" in data
        assert "tts" in data
        assert "translate" in data


class TestStatusRoute:
    """/status 路由测试（HTMX 片段）。"""

    def test_status_returns_html(self, client: TestClient) -> None:
        r = client.get("/status")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]

    def test_status_contains_profile(self, client: TestClient) -> None:
        r = client.get("/status")
        # 当前机器应自动检测到 GPU 配置档
        assert "gpu" in r.text or "cpu" in r.text
