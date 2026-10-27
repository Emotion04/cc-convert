# SPDX-License-Identifier: Apache-2.0
"""Tests for upstream HTTP client reuse and timeout configuration.

Workers used to create a new ``httpx.AsyncClient`` for every request and, on
the streaming paths, never closed it. That meant a fresh TCP+TLS handshake per
request (visible latency, and more exposure to flaky network paths) and an
unbounded number of leaked connection pools.
"""

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from cc_convert import router as router_module


def _request(state=None):
    return SimpleNamespace(app=SimpleNamespace(state=state or SimpleNamespace()))


def test_timeout_honours_request_timeout():
    """settings.request_timeout must drive the read timeout."""
    settings = SimpleNamespace(request_timeout=42.0)

    timeout = router_module.build_upstream_timeout(settings)

    assert timeout.read == 42.0
    assert timeout.connect == 15.0


def test_timeout_falls_back_when_unset():
    """A missing or invalid request_timeout falls back to 300 s."""
    assert router_module.build_upstream_timeout(None).read == 300.0
    assert router_module.build_upstream_timeout(SimpleNamespace()).read == 300.0
    assert (
        router_module.build_upstream_timeout(SimpleNamespace(request_timeout=None)).read
        == 300.0
    )
    assert (
        router_module.build_upstream_timeout(SimpleNamespace(request_timeout="bad")).read
        == 300.0
    )


def test_uses_shared_client_from_app_state():
    """When the app owns a client, requests reuse it and do not close it."""
    shared = object()
    request = _request(SimpleNamespace(http_client=shared))

    client, owned = router_module._get_http_client(request)

    assert client is shared
    assert owned is False


def test_falls_back_to_a_private_client():
    """Without a shared client, one is created and marked as caller-owned."""
    client, owned = router_module._get_http_client(_request())

    assert isinstance(client, httpx.AsyncClient)
    assert owned is True
    asyncio.run(client.aclose())


@pytest.mark.asyncio
async def test_closing_stream_closes_owned_client():
    class FakeClient:
        def __init__(self):
            self.closed = False

        async def aclose(self):
            self.closed = True

    async def gen():
        yield "a"
        yield "b"

    client = FakeClient()
    chunks = [c async for c in router_module._closing_stream(gen(), client, True)]

    assert chunks == ["a", "b"]
    assert client.closed is True


@pytest.mark.asyncio
async def test_closing_stream_leaves_shared_client_open():
    class FakeClient:
        def __init__(self):
            self.closed = False

        async def aclose(self):
            self.closed = True

    async def gen():
        yield "a"

    client = FakeClient()
    _ = [c async for c in router_module._closing_stream(gen(), client, False)]

    assert client.closed is False
