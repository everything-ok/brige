"""
测试 FastAPI 接口层（不启动真实浏览器）
"""
import sys
import json
import pytest
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture
def test_client(mock_config):
    """创建 FastAPI 测试客户端"""
    from fastapi.testclient import TestClient

    # patch 所有外部依赖，避免启动浏览器
    with patch("main.config", mock_config), \
         patch("main.browser_manager") as mock_bm, \
         patch("main.rate_limiter") as mock_rl, \
         patch("main.security_manager") as mock_sm, \
         patch("main.load_balancer") as mock_lb, \
         patch("main.validator") as mock_v:

        mock_bm.start = AsyncMock()
        mock_bm.stop = AsyncMock()
        mock_bm.clear_site = AsyncMock()

        import main as main_module
        main_module.config = mock_config
        main_module.rate_limiter = mock_rl
        main_module.security_manager = mock_sm
        main_module.load_balancer = mock_lb
        main_module.validator = mock_v
        main_module._providers = {}

        client = TestClient(main_module.app, raise_server_exceptions=False)
        yield client, mock_config, mock_lb, mock_rl, mock_v


class TestHealthEndpoint:

    def test_health_returns_ok(self, test_client):
        client, *_ = test_client
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "timestamp" in data

    def test_root_returns_site_list(self, test_client):
        client, mock_config, mock_lb, *_ = test_client
        mock_lb.get_available_models.return_value = ["gpt-4o", "claude-3-5-sonnet"]

        resp = client.get("/")
        assert resp.status_code == 200
        data = resp.json()
        assert "sites" in data
        assert "models" in data
        assert "poe" in data["sites"]


class TestModelsEndpoint:

    def test_list_models_no_auth_when_key_empty(self, test_client):
        client, mock_config, *_ = test_client
        mock_config.API_KEY = ""

        resp = client.get("/v1/models")
        assert resp.status_code == 200
        data = resp.json()
        assert data["object"] == "list"
        assert isinstance(data["data"], list)

    def test_list_models_with_valid_key(self, test_client):
        client, mock_config, *_ = test_client
        mock_config.API_KEY = "test-key"

        resp = client.get("/v1/models", headers={"Authorization": "Bearer test-key"})
        assert resp.status_code == 200

    def test_list_models_with_invalid_key(self, test_client):
        client, mock_config, *_ = test_client
        mock_config.API_KEY = "test-key"

        resp = client.get("/v1/models", headers={"Authorization": "Bearer wrong-key"})
        assert resp.status_code == 401

    def test_list_models_contains_site_models(self, test_client):
        client, mock_config, *_ = test_client
        mock_config.API_KEY = ""

        resp = client.get("/v1/models")
        data = resp.json()
        model_ids = [m["id"] for m in data["data"]]
        assert "poe/gpt-4o" in model_ids
        assert "poe/claude-3-5-sonnet" in model_ids


class TestChatCompletionsEndpoint:

    def _make_provider_mock(self, response_chunks):
        """创建返回指定 chunks 的 provider mock"""
        provider = MagicMock()

        async def mock_chat(*args, **kwargs):
            for chunk in response_chunks:
                yield chunk

        provider.chat = mock_chat
        return provider

    def test_non_stream_response(self, test_client):
        client, mock_config, mock_lb, *_ = test_client
        mock_config.API_KEY = ""
        mock_lb.select_site = AsyncMock(return_value="poe")

        provider = self._make_provider_mock(["Hello", " world"])

        with patch("main.get_provider", return_value=provider):
            resp = client.post("/v1/chat/completions", json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": False,
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["object"] == "chat.completion"
        assert data["choices"][0]["message"]["content"] == "Hello world"
        assert data["choices"][0]["finish_reason"] == "stop"
        assert "usage" in data

    def test_non_stream_with_site_in_model(self, test_client):
        client, mock_config, mock_lb, *_ = test_client
        mock_config.API_KEY = ""

        provider = self._make_provider_mock(["Response text"])

        with patch("main.get_provider", return_value=provider):
            resp = client.post("/v1/chat/completions", json={
                "model": "poe/gpt-4o",
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": False,
            })

        assert resp.status_code == 200
        data = resp.json()
        assert "poe" in data["model"]

    def test_non_stream_error_response(self, test_client):
        client, mock_config, mock_lb, *_ = test_client
        mock_config.API_KEY = ""
        mock_lb.select_site = AsyncMock(return_value="poe")

        provider = self._make_provider_mock(["[ERROR] Something went wrong"])

        with patch("main.get_provider", return_value=provider):
            resp = client.post("/v1/chat/completions", json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": False,
            })

        assert resp.status_code == 500

    def test_no_available_site_returns_503(self, test_client):
        client, mock_config, mock_lb, *_ = test_client
        mock_config.API_KEY = ""
        mock_lb.select_site = AsyncMock(return_value=None)

        resp = client.post("/v1/chat/completions", json={
            "model": "unknown-model",
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": False,
        })

        assert resp.status_code == 503

    def test_stream_response_sse_format(self, test_client):
        client, mock_config, mock_lb, *_ = test_client
        mock_config.API_KEY = ""
        mock_lb.select_site = AsyncMock(return_value="poe")

        provider = self._make_provider_mock(["Hello", " world"])

        with patch("main.get_provider", return_value=provider):
            resp = client.post("/v1/chat/completions", json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": True,
            })

        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")

        # 解析 SSE 数据
        lines = resp.text.strip().split("\n")
        data_lines = [l for l in lines if l.startswith("data: ")]
        assert len(data_lines) >= 2  # 至少有内容 + [DONE]

        # 最后一行应该是 [DONE]
        assert data_lines[-1] == "data: [DONE]"

        # 中间的数据应该是有效 JSON
        for line in data_lines[:-1]:
            payload = json.loads(line[6:])
            assert "choices" in payload
            assert payload["object"] == "chat.completion.chunk"

    def test_stream_response_content_assembled(self, test_client):
        client, mock_config, mock_lb, *_ = test_client
        mock_config.API_KEY = ""
        mock_lb.select_site = AsyncMock(return_value="poe")

        provider = self._make_provider_mock(["Hello", ", ", "world", "!"])

        with patch("main.get_provider", return_value=provider):
            resp = client.post("/v1/chat/completions", json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": True,
            })

        lines = resp.text.strip().split("\n")
        data_lines = [l for l in lines if l.startswith("data: ") and l != "data: [DONE]"]

        content_parts = []
        for line in data_lines:
            payload = json.loads(line[6:])
            delta = payload["choices"][0]["delta"]
            if "content" in delta:
                content_parts.append(delta["content"])

        assert "".join(content_parts) == "Hello, world!"

    def test_auth_required_when_key_set(self, test_client):
        client, mock_config, *_ = test_client
        mock_config.API_KEY = "secret"

        resp = client.post("/v1/chat/completions", json={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hi"}],
        })
        assert resp.status_code == 401

    def test_auth_passes_with_correct_key(self, test_client):
        client, mock_config, mock_lb, *_ = test_client
        mock_config.API_KEY = "secret"
        mock_lb.select_site = AsyncMock(return_value="poe")

        provider = self._make_provider_mock(["OK"])

        with patch("main.get_provider", return_value=provider):
            resp = client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o",
                    "messages": [{"role": "user", "content": "Hi"}],
                    "stream": False,
                },
                headers={"Authorization": "Bearer secret"},
            )

        assert resp.status_code == 200


class TestAdminEndpoints:

    def test_admin_status(self, test_client):
        client, mock_config, mock_lb, mock_rl, *_ = test_client
        mock_config.API_KEY = ""
        mock_rl.get_status.return_value = {"poe": {"requests_last_minute": 0}}

        resp = client.get("/admin/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "rate_limits" in data
        assert "sites" in data

    def test_admin_clear_profile(self, test_client):
        client, mock_config, *_ = test_client
        mock_config.API_KEY = ""

        with patch("main.browser_manager") as mock_bm:
            mock_bm.clear_site = AsyncMock()
            resp = client.post("/admin/clear-profile/poe")

        assert resp.status_code == 200
        assert resp.json()["cleared"] == "poe"
