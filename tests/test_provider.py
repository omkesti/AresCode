"""Tests for the OpenAI-compatible streaming provider (TASKS 1.1-1.2).

Uses httpx.MockTransport so no live model server is needed.
"""

from __future__ import annotations

import json

import httpx
import pytest

from arescode.providers.base import ProviderError
from arescode.providers.openai_compat import OpenAICompatProvider

SSE_TWO_DELTAS = (
    'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":", world"}}]}\n\n'
    "data: [DONE]\n\n"
)


def _provider(handler, **kwargs) -> OpenAICompatProvider:
    return OpenAICompatProvider(
        base_url="http://test/v1",
        model=kwargs.pop("model", "m"),
        num_ctx=kwargs.pop("num_ctx", 16384),
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


async def test_stream_yields_content_deltas():
    def handler(request):
        return httpx.Response(
            200, text=SSE_TWO_DELTAS, headers={"content-type": "text/event-stream"}
        )

    provider = _provider(handler)
    chunks = [c.content async for c in provider.chat([{"role": "user", "content": "hi"}])]
    assert chunks == ["Hello", ", world"]


async def test_complete_concatenates_stream():
    def handler(request):
        return httpx.Response(200, text=SSE_TWO_DELTAS)

    provider = _provider(handler)
    assert await provider.complete([{"role": "user", "content": "hi"}]) == "Hello, world"


async def test_body_carries_model_stream_and_num_ctx():
    seen: dict = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, text="data: [DONE]\n\n")

    provider = _provider(handler, model="qwen2.5-coder:14b-instruct", num_ctx=12345)
    await provider.complete([{"role": "user", "content": "hi"}])

    assert seen["body"]["model"] == "qwen2.5-coder:14b-instruct"
    assert seen["body"]["stream"] is True
    assert seen["body"]["options"]["num_ctx"] == 12345


async def test_http_error_becomes_provider_error():
    def handler(request):
        return httpx.Response(500, text="internal boom")

    provider = _provider(handler)
    with pytest.raises(ProviderError, match="500"):
        [c async for c in provider.chat([{"role": "user", "content": "hi"}])]


async def test_connect_error_becomes_provider_error():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    provider = _provider(handler)
    with pytest.raises(ProviderError, match="Cannot reach"):
        [c async for c in provider.chat([{"role": "user", "content": "hi"}])]
