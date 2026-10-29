# SPDX-License-Identifier: Apache-2.0
"""End-to-end collection: request in, exactly one correct bucket row out.

Covers the采集矩阵 rows that matter most — a missed collection point shows
up here as a missing or wrong row ("面板数字比账单少").
"""

import json
from contextlib import asynccontextmanager
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from cc_convert.config import Settings
from cc_convert.main import create_proxy_app
from cc_convert.router import _openai_stream_proxy, _stream_response
from cc_convert.usage.ledger import UsageCollector
from cc_convert.usage.store import UsageStore


def _settings() -> Settings:
    return Settings(
        openai_api_key="test-key",
        openai_base_url="https://relay.example/v1",
        request_timeout=30.0,
        api_key=None,
        tavily_api_key=None,
    )


def _read_rows(db_path):
    store = UsageStore(db_path)
    try:
        return store.query(0, 4_000_000_000)
    finally:
        store.close()


_UPSTREAM_COMPLETION = {
    "id": "c1",
    "object": "chat.completion",
    "created": 1,
    "model": "gpt-x",
    "choices": [
        {
            "index": 0,
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": "Hello"},
        }
    ],
    "usage": {
        "prompt_tokens": 1000,
        "completion_tokens": 200,
        "total_tokens": 1200,
        "prompt_tokens_details": {"cached_tokens": 800},
    },
}


class TestMessagesNonStreaming:
    def test_usage_recorded_with_cache_subtraction(self, tmp_path):
        db = tmp_path / "usage.db"
        app = create_proxy_app(_settings(), "worker", "tok", usage_db_path=db)
        client = TestClient(app)
        mock_response = httpx.Response(
            200,
            json=_UPSTREAM_COMPLETION,
            request=httpx.Request("POST", "https://relay.example/v1/chat/completions"),
        )
        with patch("httpx.AsyncClient.post", return_value=mock_response):
            response = client.post(
                "/v1/messages",
                json={
                    "model": "claude-x",
                    "max_tokens": 16,
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
        assert response.status_code == 200
        app.state.usage.flush_once()
        rows = _read_rows(db)
        assert len(rows) == 1
        row = rows[0]
        assert row["instance_id"] == "worker"
        assert row["model"] == "claude-x"
        assert row["client_model"] == "claude-x"
        assert row["endpoint"] == "/v1/messages"
        assert row["requests"] == 1
        # chat semantics: prompt_tokens includes the cached read
        assert row["input_tokens"] == 200
        assert row["cache_read"] == 800
        assert row["output_tokens"] == 200
        assert row["unreported"] == 0
        assert row["errors"] == 0

    def test_upstream_error_counts_as_error(self, tmp_path):
        db = tmp_path / "usage.db"
        app = create_proxy_app(_settings(), "worker", "tok", usage_db_path=db)
        client = TestClient(app)
        mock_response = httpx.Response(
            500,
            json={"error": {"message": "boom"}},
            request=httpx.Request("POST", "https://relay.example/v1/chat/completions"),
        )
        with patch("httpx.AsyncClient.post", return_value=mock_response):
            response = client.post(
                "/v1/messages",
                json={
                    "model": "claude-x",
                    "max_tokens": 16,
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
        assert response.status_code == 500
        app.state.usage.flush_once()
        rows = _read_rows(db)
        assert len(rows) == 1
        assert rows[0]["errors"] == 1
        # An errored request is still a request (per-request metrics divide
        # by this column), but it is never "unreported usage".
        assert rows[0]["requests"] == 1
        assert rows[0]["unreported"] == 0


class TestMessagesStreaming:
    @staticmethod
    def _run_stream(tmp_path, lines, instance="worker"):
        db = tmp_path / "usage.db"
        collector = UsageCollector(instance, db_path=db)
        recorder = collector.recorder("/v1/messages", "gpt-x", "claude-x")

        class _Response:
            status_code = 200

            async def aread(self):
                return b""

            async def aiter_lines(self):
                for line in lines:
                    yield line

        @asynccontextmanager
        async def _ctx(*args, **kwargs):
            yield _Response()

        class _Client:
            def stream(self, *args, **kwargs):
                return _ctx()

        async def _consume():
            async for _ in _stream_response(
                _Client(),  # type: ignore[arg-type]
                "https://relay.example/v1/chat/completions",
                {},
                {"model": "gpt-x"},
                "gpt-x",
                usage_recorder=recorder,
            ):
                pass

        import asyncio

        asyncio.run(_consume())
        collector.flush_once()
        collector.close()
        return _read_rows(db)

    def test_usage_chunk_captured(self, tmp_path):
        rows = self._run_stream(
            tmp_path,
            [
                'data: {"id":"1","choices":[{"index":0,"delta":{"content":"Hi"}}]}',
                "data: {"
                '"id":"1","choices":[],'
                '"usage":{"prompt_tokens":100,"completion_tokens":7,'
                '"prompt_tokens_details":{"cached_tokens":60}}}',
                "data: [DONE]",
            ],
        )
        assert len(rows) == 1
        row = rows[0]
        assert row["requests"] == 1
        assert row["input_tokens"] == 40
        assert row["cache_read"] == 60
        assert row["output_tokens"] == 7
        assert row["unreported"] == 0

    def test_missing_usage_counts_as_unreported(self, tmp_path):
        rows = self._run_stream(
            tmp_path,
            [
                'data: {"id":"1","choices":[{"index":0,"delta":{"content":"Hi"}}]}',
                "data: [DONE]",
            ],
        )
        assert len(rows) == 1
        assert rows[0]["requests"] == 1
        assert rows[0]["unreported"] == 1
        assert rows[0]["input_tokens"] == 0


class TestChatPassthrough:
    def test_non_streaming_recorded(self, tmp_path):
        db = tmp_path / "usage.db"
        app = create_proxy_app(_settings(), "worker", "tok", usage_db_path=db)
        client = TestClient(app)
        mock_response = httpx.Response(
            200,
            json=_UPSTREAM_COMPLETION,
            request=httpx.Request("POST", "https://relay.example/v1/chat/completions"),
        )
        with patch("httpx.AsyncClient.post", return_value=mock_response):
            response = client.post(
                "/v1/chat/completions",
                json={"model": "gpt-x", "messages": [{"role": "user", "content": "hi"}]},
            )
        assert response.status_code == 200
        app.state.usage.flush_once()
        rows = _read_rows(db)
        assert len(rows) == 1
        row = rows[0]
        assert row["endpoint"] == "/v1/chat/completions"
        assert row["input_tokens"] == 200
        assert row["cache_read"] == 800

    @staticmethod
    def _run_proxy_stream(tmp_path, lines, *, strip):
        db = tmp_path / "usage.db"
        collector = UsageCollector("worker", db_path=db)
        recorder = collector.recorder("/v1/chat/completions", "gpt-x", "gpt-x")

        class _Response:
            status_code = 200

            async def aiter_lines(self):
                for line in lines:
                    yield line

        @asynccontextmanager
        async def _ctx(*args, **kwargs):
            yield _Response()

        class _Client:
            def stream(self, *args, **kwargs):
                return _ctx()

        async def _consume():
            chunks = []
            async for chunk in _openai_stream_proxy(
                _Client(),  # type: ignore[arg-type]
                "https://relay.example/v1/chat/completions",
                {},
                {},
                usage_recorder=recorder,
                strip_usage_chunks=strip,
            ):
                chunks.append(chunk)
            return chunks

        import asyncio

        chunks = asyncio.run(_consume())
        collector.flush_once()
        collector.close()
        return chunks, _read_rows(db)

    _SSE = [
        'data: {"id":"1","choices":[{"index":0,"delta":{"content":"Hi"}}]}',
        'data: {"id":"1","choices":[],"usage":{"prompt_tokens":50,"completion_tokens":3}}',
        "data: [DONE]",
    ]

    def test_usage_captured_and_stripped_when_injected(self, tmp_path):
        chunks, rows = self._run_proxy_stream(tmp_path, self._SSE, strip=True)
        assert len(rows) == 1
        assert rows[0]["input_tokens"] == 50
        assert rows[0]["output_tokens"] == 3
        # The usage-only chunk we asked the upstream for must not reach the
        # client that never asked for it.
        assert not any('"usage"' in c for c in chunks)

    def test_usage_chunk_kept_when_client_requested_it(self, tmp_path):
        chunks, rows = self._run_proxy_stream(tmp_path, self._SSE, strip=False)
        assert rows[0]["requests"] == 1
        assert any('"usage"' in c for c in chunks)


class TestResponsesPassthrough:
    def test_response_completed_usage_captured(self, tmp_path):
        db = tmp_path / "usage.db"
        collector = UsageCollector("worker", db_path=db)
        recorder = collector.recorder("/v1/responses", "gpt-x", "gpt-x")

        completed = {
            "type": "response.completed",
            "response": {
                "id": "r1",
                "object": "response",
                "usage": {
                    "input_tokens": 900,
                    "output_tokens": 40,
                    "input_tokens_details": {"cached_tokens": 500},
                },
            },
        }
        lines = [
            'data: {"type":"response.created","response":{"id":"r1"}}',
            f"data: {json.dumps(completed)}",
        ]

        class _Response:
            status_code = 200

            async def aiter_lines(self):
                for line in lines:
                    yield line

        @asynccontextmanager
        async def _ctx(*args, **kwargs):
            yield _Response()

        class _Client:
            def stream(self, *args, **kwargs):
                return _ctx()

        async def _consume():
            async for _ in _openai_stream_proxy(
                _Client(),  # type: ignore[arg-type]
                "https://relay.example/v1/responses",
                {},
                {},
                usage_recorder=recorder,
            ):
                pass

        import asyncio

        asyncio.run(_consume())
        collector.flush_once()
        collector.close()
        rows = _read_rows(db)
        assert len(rows) == 1
        # Responses semantics: input_tokens includes the cached read.
        assert rows[0]["input_tokens"] == 400
        assert rows[0]["cache_read"] == 500
        assert rows[0]["output_tokens"] == 40


class TestStatusEndpoint:
    def test_status_includes_usage_summary(self, tmp_path):
        app = create_proxy_app(_settings(), "worker", "tok", usage_db_path=tmp_path / "u.db")
        client = TestClient(app)
        response = client.get(
            "/_ccconvert/status", headers={"Authorization": "Bearer tok"}
        )
        assert response.status_code == 200
        body = response.json()
        # Legacy fields intact (supervisor health check depends on them).
        for legacy in (
            "instance_id", "revision", "started_at", "uptime",
            "total_requests", "total_errors", "error_rate", "status",
        ):
            assert legacy in body
        assert body["usage"]["requests"] == 0
