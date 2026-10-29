# SPDX-License-Identifier: Apache-2.0
"""Regression tests for the defects listed in docs/不变量与陷阱.md §I.

Each test names the entry it covers so a future reader can tell why the
behaviour matters, not just what it does.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from cc_convert.config import Settings
from cc_convert.converter import convert_anthropic_to_openai
from cc_convert.main import create_app

_CHAT_COMPLETION = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "created": 1,
    "model": "m",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "ok"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11},
}


def _settings(**overrides) -> Settings:
    base = dict(
        openai_api_key="test-key",
        openai_base_url="https://up.test/v1",
        request_timeout=30.0,
        tavily_api_key=None,
        tongxiao_api_key=None,
    )
    base.update(overrides)
    return Settings(**base)


def _client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))


def _mock_client(payload: dict) -> MagicMock:
    client = MagicMock()
    client.post = AsyncMock(
        return_value=httpx.Response(
            200,
            json=payload,
            request=httpx.Request("POST", "https://up.test/v1/x"),
        )
    )
    client.aclose = AsyncMock()
    return client


class TestConverterDefects:
    def test_null_repetition_penalty_is_not_sent(self):
        """I7: strict upstreams reject the key even when it is null."""
        params = convert_anthropic_to_openai(
            {"model": "m", "max_tokens": 8, "messages": [{"role": "user", "content": "hi"}]},
            thinking_history="turn",
        )
        assert "repetition_penalty" not in params

    def test_explicit_repetition_penalty_is_kept(self):
        params = convert_anthropic_to_openai(
            {
                "model": "m",
                "max_tokens": 8,
                "messages": [{"role": "user", "content": "hi"}],
                "repetition_penalty": 1.1,
            },
            thinking_history="turn",
        )
        assert params["repetition_penalty"] == 1.1

    def test_tool_without_a_name_is_dropped(self):
        params = convert_anthropic_to_openai(
            {
                "model": "m",
                "max_tokens": 8,
                "messages": [{"role": "user", "content": "hi"}],
                "tools": [{"type": "some_vendor_tool", "description": "x"}],
            },
            thinking_history="turn",
        )
        assert "tools" not in params


class TestServerToolGating:
    def test_web_search_without_provider_is_not_forwarded(self):
        """I1: no search provider → the tool must not reach the upstream."""
        settings = _settings()
        fake = _mock_client(_CHAT_COMPLETION)

        with patch("cc_convert.router.httpx.AsyncClient", return_value=fake):
            response = _client(settings).post(
                "/v1/messages",
                json={
                    "model": "claude",
                    "max_tokens": 16,
                    "messages": [{"role": "user", "content": "search the web"}],
                    "tools": [
                        {
                            "type": "web_search_20250305",
                            "name": "web_search",
                            "max_uses": 3,
                        }
                    ],
                },
            )

        assert response.status_code == 200
        sent = fake.post.call_args.kwargs["json"]
        assert "tools" not in sent


class TestResponsesUpstreamDefects:
    def test_response_payload_without_object_still_converts(self):
        """I3: some relays omit ``object`` but still answer in Responses shape."""
        settings = _settings(upstream_format="responses")
        payload = {
            "id": "resp_1",
            "model": "m",
            "created_at": 1,
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "hello"}],
                }
            ],
            "usage": {
                "input_tokens": 5,
                "output_tokens": 1,
                "input_tokens_details": {"cached_tokens": 0},
            },
        }
        fake = _mock_client(payload)

        with patch("cc_convert.router.httpx.AsyncClient", return_value=fake):
            response = _client(settings).post(
                "/v1/messages",
                json={
                    "model": "claude",
                    "max_tokens": 16,
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )

        assert response.status_code == 200
        assert response.json()["content"][0]["text"] == "hello"

    def test_responses_endpoint_passes_through_when_web_search_is_present(self):
        """I5: a chat-shaped body must never be posted to a Responses upstream."""
        settings = _settings(upstream_format="responses")
        proxy = AsyncMock(return_value=JSONResponse(content={"ok": True}))

        with patch("cc_convert.router._proxy_responses_passthrough", new=proxy):
            response = _client(settings).post(
                "/v1/responses",
                json={
                    "model": "m",
                    "input": "hi",
                    "tools": [{"type": "web_search"}],
                },
            )

        assert response.status_code == 200
        proxy.assert_awaited_once()


class TestSettingsEnvironment:
    def test_environment_variables_are_ignored(self, monkeypatch):
        """I8: the config file is the single source of truth."""
        monkeypatch.setenv("OPENAI_API_KEY", "from-env")
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("OPENAI_BASE_URL", "https://env.test/v1")

        settings = Settings()

        assert settings.openai_api_key is None
        assert settings.log_level == "INFO"
        assert settings.openai_base_url == "https://api.openai.com/v1"

    def test_explicit_values_still_win(self):
        settings = Settings(openai_api_key="explicit")
        assert settings.openai_api_key == "explicit"


class TestRestartPort:
    def test_restart_forwards_port(self):
        from cc_convert import daemon

        with patch.object(daemon, "stop_daemon", return_value=True), patch(
            "time.sleep"
        ), patch.object(daemon, "start_daemon", return_value=True) as start:
            daemon.restart_daemon(port=18888)

        start.assert_called_once_with(18888)
