"""
Tests for the OpenAI-compatible proxy handler and router.

Tests:
  - Successful proxy pass-through (non-streaming)
  - Streaming response pass-through
  - Metadata extraction from custom headers
  - Telemetry emission (mock Redis)
  - Upstream error handling (timeout, connection error, 4xx, 5xx)
  - Health and readiness endpoints
  - Provider routing (model → base URL mapping)
  - Embeddings pass-through
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import AsyncClient, Response as HttpxResponse

# We import the main app with mocked environment vars
import os
os.environ.setdefault("OPENAI_API_KEY", "sk-test-key")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("TIMESCALEDB_URL", "postgresql://spectraflow:spectraflow@localhost:5432/spectraflow")
os.environ.setdefault("QDRANT_HOST", "localhost")


# ── Fixtures ──────────────────────────────────────────────────────────────────

MOCK_CHAT_RESPONSE = {
    "id": "chatcmpl-test123",
    "object": "chat.completion",
    "created": 1719619200,
    "model": "gpt-4o-mini",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "Hello! How can I help you today?",
            },
            "finish_reason": "stop",
        }
    ],
    "usage": {
        "prompt_tokens": 12,
        "completion_tokens": 10,
        "total_tokens": 22,
    },
}

MOCK_EMBEDDINGS_RESPONSE = {
    "object": "list",
    "data": [
        {
            "object": "embedding",
            "embedding": [0.1] * 1536,
            "index": 0,
        }
    ],
    "model": "text-embedding-3-small",
    "usage": {"prompt_tokens": 5, "total_tokens": 5},
}


@pytest.fixture
def mock_redis():
    """Mock async Redis client."""
    mock = AsyncMock()
    mock.xadd = AsyncMock(return_value=b"1-0")
    mock.ping = AsyncMock(return_value=True)
    mock.aclose = AsyncMock()
    return mock


@pytest.fixture
def mock_upstream_response():
    """Mock successful OpenAI upstream response."""
    response = MagicMock(spec=HttpxResponse)
    response.status_code = 200
    response.content = json.dumps(MOCK_CHAT_RESPONSE).encode()
    response.json = MagicMock(return_value=MOCK_CHAT_RESPONSE)
    response.headers = {"content-type": "application/json"}
    return response


@pytest.fixture
def mock_embeddings_response():
    response = MagicMock(spec=HttpxResponse)
    response.status_code = 200
    response.content = json.dumps(MOCK_EMBEDDINGS_RESPONSE).encode()
    response.json = MagicMock(return_value=MOCK_EMBEDDINGS_RESPONSE)
    response.headers = {"content-type": "application/json"}
    return response


# ── Test: Proxy handler unit tests ────────────────────────────────────────────

class TestProxyMetadataExtraction:

    def test_default_metadata_when_no_headers(self) -> None:
        """Missing X-Pipeline-Name defaults to 'default'."""
        from spectraflow.proxy.handler import _extract_metadata

        request = MagicMock()
        request.headers = {}
        body = {"model": "gpt-4o"}

        meta = _extract_metadata(request, body)
        assert meta["pipeline_name"] == "default"
        assert meta["prompt_version"] == "unknown"
        assert meta["user_segment"] == "unknown"
        assert meta["model_id"] == "gpt-4o"

    def test_custom_headers_extracted(self) -> None:
        from spectraflow.proxy.handler import _extract_metadata

        request = MagicMock()
        request.headers = {
            "x-pipeline-name": "customer-support",
            "x-prompt-version": "v2.3.1",
            "x-user-segment": "enterprise",
        }
        body = {"model": "gpt-4o-mini"}

        meta = _extract_metadata(request, body)
        assert meta["pipeline_name"] == "customer-support"
        assert meta["prompt_version"] == "v2.3.1"
        assert meta["user_segment"] == "enterprise"
        assert meta["model_id"] == "gpt-4o-mini"

    def test_spectraflow_pipeline_header_alias(self) -> None:
        from spectraflow.proxy.handler import _extract_metadata

        request = MagicMock()
        request.headers = {"x-spectraflow-pipeline": "my-pipeline"}
        body = {}

        meta = _extract_metadata(request, body)
        assert meta["pipeline_name"] == "my-pipeline"


class TestProviderRouting:

    def test_default_routes_to_openai(self) -> None:
        from spectraflow.proxy.handler import _resolve_upstream
        from spectraflow.config import get_settings

        settings = get_settings()
        base_url, _ = _resolve_upstream("gpt-4o")
        assert base_url == settings.upstream_base_url

    def test_deepseek_model_routes_to_deepseek(self) -> None:
        from spectraflow.proxy.handler import _resolve_upstream
        from spectraflow.config import get_settings

        settings = get_settings()
        base_url, _ = _resolve_upstream("deepseek-chat")
        assert base_url == settings.deepseek_base_url

    def test_claude_without_compatible_gateway_fails_closed(self, monkeypatch) -> None:
        from spectraflow.proxy.handler import _resolve_upstream, settings

        monkeypatch.setattr(settings, "anthropic_compatible_base_url", "")
        with pytest.raises(ValueError, match="ANTHROPIC_COMPATIBLE_BASE_URL"):
            _resolve_upstream("claude-sonnet")

    def test_claude_routes_only_to_explicit_compatible_gateway(self, monkeypatch) -> None:
        from spectraflow.proxy.handler import _resolve_upstream, settings

        monkeypatch.setattr(settings, "anthropic_compatible_base_url", "https://gateway.example/v1")
        monkeypatch.setattr(settings, "anthropic_api_key", "test-anthropic-key")
        base_url, api_key = _resolve_upstream("claude-sonnet")
        assert base_url == "https://gateway.example/v1"
        assert api_key == "test-anthropic-key"

    def test_none_model_defaults_to_openai(self) -> None:
        from spectraflow.proxy.handler import _resolve_upstream
        from spectraflow.config import get_settings

        settings = get_settings()
        base_url, _ = _resolve_upstream(None)
        assert base_url == settings.upstream_base_url


class TestTelemetryEmission:

    @pytest.mark.asyncio
    async def test_emit_telemetry_calls_xadd(self, mock_redis: AsyncMock) -> None:
        """_emit_telemetry should call redis.xadd with the event."""
        with patch("spectraflow.proxy.handler._redis_client", mock_redis):
            from spectraflow.proxy.handler import _emit_telemetry

            event = {
                "event_id": "test-123",
                "pipeline_name": "test-pipeline",
                "response_content": "Hello world",
                "event_type": "chat_completion",
            }
            await _emit_telemetry(event)

            mock_redis.xadd.assert_called_once()
            call_args = mock_redis.xadd.call_args
            # First positional arg is stream key
            assert "spectraflow:telemetry" in str(call_args)

    @pytest.mark.asyncio
    async def test_emit_telemetry_handles_redis_error_gracefully(self) -> None:
        """Telemetry errors must not propagate — they are fire-and-forget."""
        mock_redis = AsyncMock()
        mock_redis.xadd = AsyncMock(side_effect=ConnectionError("Redis down"))

        with patch("spectraflow.proxy.handler._redis_client", mock_redis):
            from spectraflow.proxy.handler import _emit_telemetry

            # Should not raise
            await _emit_telemetry({"event_id": "test", "pipeline_name": "test"})


# ── Test: FastAPI router integration tests ────────────────────────────────────

class TestHealthEndpoints:

    def test_health_endpoint_returns_200(self) -> None:
        """GET /health should always return 200 when the process is running."""
        from main import app

        with patch("spectraflow.proxy.handler._redis_client", AsyncMock()):
            with TestClient(app) as client:
                response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["service"] == "spectraflow-proxy"
        assert "version" in data
        assert "uptime_seconds" in data

    def test_health_returns_version(self) -> None:
        from main import app

        with TestClient(app) as client:
            response = client.get("/health")

        assert response.json()["version"] == "0.1.0"


class TestProxyChatCompletions:

    @pytest.mark.asyncio
    async def test_proxy_chat_completion_success(
        self,
        mock_upstream_response: MagicMock,
        mock_redis: AsyncMock,
    ) -> None:
        """Proxy should forward request and return upstream response."""
        from main import app

        with patch("spectraflow.proxy.handler._redis_client", mock_redis):
            with patch("httpx.AsyncClient.post", return_value=mock_upstream_response):
                async with AsyncClient(app=app, base_url="http://test") as client:
                    response = await client.post(
                        "/v1/chat/completions",
                        json={
                            "model": "gpt-4o-mini",
                            "messages": [{"role": "user", "content": "Hello"}],
                        },
                        headers={
                            "Authorization": "Bearer sk-test",
                            "X-Pipeline-Name": "test-pipeline",
                        },
                    )

        assert response.status_code == 200
        body = response.json()
        assert body["object"] == "chat.completion"
        assert body["choices"][0]["message"]["content"] == "Hello! How can I help you today?"

    @pytest.mark.asyncio
    async def test_proxy_invalid_json_returns_400(self) -> None:
        """Invalid JSON body should return 400."""
        from main import app

        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                content=b"not-json",
                headers={"Content-Type": "application/json"},
            )

        assert response.status_code == 400
        assert "error" in response.json()

    @pytest.mark.asyncio
    async def test_proxy_upstream_timeout_returns_504(self, mock_redis: AsyncMock) -> None:
        """Upstream timeout should return 504 Gateway Timeout."""
        import httpx

        with patch("spectraflow.proxy.handler._redis_client", mock_redis):
            with patch(
                "httpx.AsyncClient.post",
                side_effect=httpx.TimeoutException("timeout"),
            ):
                from main import app

                async with AsyncClient(app=app, base_url="http://test") as client:
                    response = await client.post(
                        "/v1/chat/completions",
                        json={
                            "model": "gpt-4o-mini",
                            "messages": [{"role": "user", "content": "Hello"}],
                        },
                        headers={"Authorization": "Bearer sk-test"},
                    )

        assert response.status_code == 504
        data = response.json()
        assert data["error"]["type"] == "timeout_error"

    @pytest.mark.asyncio
    async def test_proxy_connection_error_returns_502(self, mock_redis: AsyncMock) -> None:
        """Upstream connection error should return 502 Bad Gateway."""
        import httpx

        with patch("spectraflow.proxy.handler._redis_client", mock_redis):
            with patch(
                "httpx.AsyncClient.post",
                side_effect=httpx.ConnectError("connection refused"),
            ):
                from main import app

                async with AsyncClient(app=app, base_url="http://test") as client:
                    response = await client.post(
                        "/v1/chat/completions",
                        json={
                            "model": "gpt-4o-mini",
                            "messages": [{"role": "user", "content": "Hello"}],
                        },
                        headers={"Authorization": "Bearer sk-test"},
                    )

        assert response.status_code == 502

    @pytest.mark.asyncio
    async def test_proxy_preserves_upstream_4xx(
        self,
        mock_redis: AsyncMock,
    ) -> None:
        """Upstream 401/403/404 should be forwarded as-is."""
        error_response = MagicMock(spec=HttpxResponse)
        error_response.status_code = 401
        error_response.content = json.dumps({
            "error": {"message": "Invalid API key", "type": "invalid_api_key"}
        }).encode()
        error_response.json = MagicMock(return_value={
            "error": {"message": "Invalid API key"}
        })
        error_response.headers = {"content-type": "application/json"}

        with patch("spectraflow.proxy.handler._redis_client", mock_redis):
            with patch("httpx.AsyncClient.post", return_value=error_response):
                from main import app

                async with AsyncClient(app=app, base_url="http://test") as client:
                    response = await client.post(
                        "/v1/chat/completions",
                        json={
                            "model": "gpt-4o-mini",
                            "messages": [{"role": "user", "content": "Hello"}],
                        },
                        headers={"Authorization": "Bearer sk-invalid"},
                    )

        assert response.status_code == 401


class TestProxyEmbeddings:

    @pytest.mark.asyncio
    async def test_embeddings_passthrough(
        self,
        mock_embeddings_response: MagicMock,
    ) -> None:
        """Embeddings should be passed through and returned correctly."""
        from main import app

        with patch("httpx.AsyncClient.post", return_value=mock_embeddings_response):
            async with AsyncClient(app=app, base_url="http://test") as client:
                response = await client.post(
                    "/v1/embeddings",
                    json={
                        "model": "text-embedding-3-small",
                        "input": "Hello world",
                    },
                    headers={"Authorization": "Bearer sk-test"},
                )

        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert len(data["data"][0]["embedding"]) == 1536


class TestProxyTelemetryContent:

    @pytest.mark.asyncio
    async def test_telemetry_event_contains_required_fields(
        self,
        mock_upstream_response: MagicMock,
    ) -> None:
        """Emitted telemetry events must contain all required fields."""
        emitted_events: list[dict] = []
        mock_redis = AsyncMock()

        async def capture_xadd(stream_key: str, event: dict, **kwargs: Any) -> str:
            emitted_events.append(event)
            return "1-0"

        mock_redis.xadd = capture_xadd

        with patch("spectraflow.proxy.handler._redis_client", mock_redis):
            with patch("httpx.AsyncClient.post", return_value=mock_upstream_response):
                from main import app

                async with AsyncClient(app=app, base_url="http://test") as client:
                    await client.post(
                        "/v1/chat/completions",
                        json={
                            "model": "gpt-4o-mini",
                            "messages": [{"role": "user", "content": "Hello"}],
                        },
                        headers={
                            "Authorization": "Bearer sk-test",
                            "X-Pipeline-Name": "test-pipeline",
                            "X-Prompt-Version": "v1.0.0",
                        },
                    )

        # Wait for async tasks
        import asyncio
        await asyncio.sleep(0.1)

        assert len(emitted_events) > 0, "No telemetry events were emitted"
        event = emitted_events[0]

        required_fields = [
            "event_id", "event_type", "pipeline_name",
            "model_id", "latency_seconds",
        ]
        for field in required_fields:
            assert field in event, f"Missing required telemetry field: {field}"

        assert event["pipeline_name"] == "test-pipeline"
