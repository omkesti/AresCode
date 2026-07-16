"""Tests for the native Ollama admin client (D12).

Uses httpx.MockTransport so no live Ollama server is needed. The chat provider is tested the same
way (test_provider.py) — the admin client is deliberately a separate, isolated surface.
"""

from __future__ import annotations

import json

import httpx
import pytest

from arescode.config import Config
from arescode.providers.ollama_admin import (
    AdminUnavailable,
    ModelLoadError,
    OllamaAdmin,
    native_root,
)

TAGS_BODY = {
    "models": [
        {"name": "qwen2.5-coder:7b", "size": 4700000000,
         "details": {"quantization_level": "Q4_K_M"}},
        {"name": "qwen2.5-coder:14b-instruct", "size": 8988000000,
         "details": {"quantization_level": "Q4_K_M"}},
    ]
}
PS_BODY = {"models": [{"name": "qwen2.5-coder:14b-instruct", "size_vram": 8000000000}]}


def _admin(handler, **kwargs) -> OllamaAdmin:
    return OllamaAdmin(
        native_url=kwargs.pop("native_url", "http://test"),
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


# --- URL derivation --------------------------------------------------------


def test_native_root_strips_v1():
    assert native_root("http://localhost:11434/v1") == "http://localhost:11434"
    assert native_root("http://localhost:11434/v1/") == "http://localhost:11434"


def test_native_root_leaves_non_v1_urls():
    # A non-Ollama backend keeps its URL; its /api/* calls will 404 -> AdminUnavailable.
    assert native_root("http://some-openai-proxy/api") == "http://some-openai-proxy/api"


def test_from_config_derives_native_root():
    admin = OllamaAdmin.from_config(Config(base_url="http://localhost:11434/v1"))
    assert admin.native_url == "http://localhost:11434"


# --- list endpoints --------------------------------------------------------


async def test_list_installed_parses_name_size_quant():
    def handler(request):
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json=TAGS_BODY)

    models = await _admin(handler).list_installed()
    assert [m.name for m in models] == ["qwen2.5-coder:7b", "qwen2.5-coder:14b-instruct"]
    assert models[1].quantization == "Q4_K_M"
    assert round(models[1].size_gb, 1) == 9.0


async def test_list_loaded_parses_ps():
    def handler(request):
        assert request.url.path == "/api/ps"
        return httpx.Response(200, json=PS_BODY)

    loaded = await _admin(handler).list_loaded()
    assert [m.name for m in loaded] == ["qwen2.5-coder:14b-instruct"]
    assert loaded[0].size_vram == 8000000000


# --- unload / warmup use the native generate endpoint ----------------------


async def test_unload_posts_keep_alive_zero():
    seen: dict = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"done": True, "done_reason": "unload"})

    await _admin(handler).unload("qwen2.5-coder:7b")
    assert seen["path"] == "/api/generate"
    assert seen["body"] == {"model": "qwen2.5-coder:7b", "keep_alive": 0}


async def test_warmup_sends_empty_prompt_with_num_ctx_and_times_it():
    seen: dict = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"done": True, "done_reason": "load"})

    seconds = await _admin(handler).warmup("qwen2.5-coder:14b-instruct", num_ctx=8192)
    assert seen["body"]["model"] == "qwen2.5-coder:14b-instruct"
    assert seen["body"]["prompt"] == ""  # empty prompt = load-only, no generation
    assert seen["body"]["options"]["num_ctx"] == 8192
    assert seconds >= 0.0


# --- graceful degradation --------------------------------------------------


async def test_404_becomes_admin_unavailable():
    def handler(request):
        return httpx.Response(404, text="not found")

    with pytest.raises(AdminUnavailable):
        await _admin(handler).list_installed()


async def test_connect_error_becomes_admin_unavailable():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(AdminUnavailable):
        await _admin(handler).list_loaded()


async def test_error_response_carries_status_code():
    def handler(request):
        return httpx.Response(500, text="boom")

    with pytest.raises(AdminUnavailable) as excinfo:
        await _admin(handler).list_installed()
    assert excinfo.value.status == 500  # so callers can tell a 5xx from an absent endpoint


# --- warmup: a load-time crash is a ModelLoadError, not "admin unavailable" -----------------


async def test_warmup_5xx_becomes_model_load_error():
    # This is exactly the reported failure: a too-large model crashes llama-server, Ollama returns
    # HTTP 500 with a CUDA message. That must surface as a hard load failure, carrying the detail.
    crash = '{"error":{"message":"llama-server terminated: CUDA error: shared object init failed"}}'

    def handler(request):
        return httpx.Response(500, text=crash)

    with pytest.raises(ModelLoadError) as excinfo:
        await _admin(handler).warmup("qwen2.5-coder:14b-instruct", num_ctx=8192)
    assert "CUDA error" in str(excinfo.value)  # backend's own message is preserved


async def test_warmup_404_stays_admin_unavailable():
    # A 404 (absent endpoint / non-Ollama backend) is degrade-gracefully, not a load failure.
    def handler(request):
        return httpx.Response(404, text="not found")

    with pytest.raises(AdminUnavailable) as excinfo:
        await _admin(handler).warmup("qwen2.5-coder:14b-instruct", num_ctx=8192)
    assert not isinstance(excinfo.value, ModelLoadError)
