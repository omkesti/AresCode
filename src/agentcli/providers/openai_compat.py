"""OpenAI-compatible streaming provider over httpx.

Targets Ollama's ``/v1/chat/completions`` by default (num_ctx / temperature / keep_alive are
passed as options); the same surface works for LM Studio, vLLM, and cloud backends with a
config change (context.md §4.7, TASKS 1.2).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import TYPE_CHECKING, Any

import httpx

from agentcli.providers.base import Chunk, ModelProvider, ProviderError, WireMessage

if TYPE_CHECKING:
    from agentcli.config import Config


class OpenAICompatProvider(ModelProvider):
    """Streams chat completions from any OpenAI-compatible server."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        num_ctx: int,
        temperature: float = 0.1,
        keep_alive: str = "30m",
        request_timeout: float = 120.0,
        api_key: str = "ollama",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.num_ctx = num_ctx
        self.temperature = temperature
        self.keep_alive = keep_alive
        self.request_timeout = request_timeout
        self.api_key = api_key
        self._transport = transport

    @classmethod
    def from_config(
        cls, config: Config, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> OpenAICompatProvider:
        return cls(
            base_url=config.base_url,
            model=config.model,
            num_ctx=config.num_ctx,
            temperature=config.temperature,
            request_timeout=config.request_timeout,
            transport=transport,
        )

    def _build_body(self, messages: Sequence[WireMessage], opts: dict[str, Any]) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": opts.pop("model", None) or self.model,
            "messages": list(messages),
            "stream": True,
            "temperature": opts.pop("temperature", self.temperature),
            "keep_alive": opts.pop("keep_alive", self.keep_alive),
            # num_ctx is the single most important Ollama option — see context.md §4.7.
            "options": {"num_ctx": opts.pop("num_ctx", self.num_ctx)},
        }
        body.update(opts)  # any remaining passthrough options (e.g. max_tokens)
        return body

    async def chat(self, messages: Sequence[WireMessage], **opts: Any) -> AsyncIterator[Chunk]:
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        body = self._build_body(messages, opts)
        timeout = httpx.Timeout(self.request_timeout, connect=10.0)

        client_kwargs: dict[str, Any] = {"timeout": timeout, "headers": headers}
        if self._transport is not None:
            client_kwargs["transport"] = self._transport

        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                async with client.stream("POST", url, json=body) as resp:
                    if resp.status_code >= 400:
                        detail = (await resp.aread()).decode("utf-8", "replace")[:500]
                        raise ProviderError(
                            f"Model server returned HTTP {resp.status_code}: {detail.strip()}"
                        )
                    async for line in resp.aiter_lines():
                        chunk = _parse_sse_line(line)
                        if chunk is None:
                            continue
                        if chunk.done:
                            return
                        yield chunk
        except httpx.ConnectError as exc:
            raise ProviderError(
                f"Cannot reach the model server at {self.base_url}. "
                "Is it running?  Start Ollama with:  ollama serve"
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderError(
                f"The model server timed out after {self.request_timeout:.0f}s."
            ) from exc
        except httpx.HTTPError as exc:  # any other transport-level failure
            raise ProviderError(f"HTTP error talking to the model server: {exc}") from exc


def _parse_sse_line(line: str) -> Chunk | None:
    """Turn one SSE line into a Chunk, or None if it carries no content."""
    line = line.strip()
    if not line or not line.startswith("data:"):
        return None
    data = line[len("data:") :].strip()
    if data == "[DONE]":
        return Chunk(content="", done=True)
    try:
        obj = json.loads(data)
    except json.JSONDecodeError:
        return None
    choices = obj.get("choices") or []
    if not choices:
        return None
    content = (choices[0].get("delta") or {}).get("content")
    if not content:
        return None
    return Chunk(content=content)
