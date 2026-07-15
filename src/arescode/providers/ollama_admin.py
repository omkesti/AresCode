"""Ollama-native admin client: model residency control the OpenAI-compat surface can't do.

The chat path talks to ``/v1/chat/completions`` (D5) and stays fully portable. But listing what
is installed, seeing what is resident in VRAM, and *explicitly* unloading a model before loading
another are native-only operations — Ollama exposes them under ``/api/*``, not ``/v1``. This
client owns exactly those four calls and nothing else:

- :meth:`list_installed` — GET ``/api/tags`` (name, size, quantization)
- :meth:`list_loaded`    — GET ``/api/ps``   (what is in VRAM right now)
- :meth:`unload`         — POST ``/api/generate`` ``{"keep_alive": 0}`` (evict from VRAM)
- :meth:`warmup`         — POST ``/api/generate`` (empty prompt) to force a load; returns seconds

**Isolation is deliberate (D12).** The chat :class:`~arescode.providers.base.ModelProvider` must
never import this module, so the native dependency stays quarantined to the admin path. When the
configured backend is not Ollama (base_url has no ``/v1`` to strip, or ``/api/*`` 404s / refuses),
every method raises :class:`AdminUnavailable`; the caller (:class:`ModelManager`) treats that as
"degrade gracefully" — a switch still changes the model name, and list/unload become no-ops.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from arescode.config import Config

# Keep the model warm after a warmup so the very next chat call serves it without a reload.
# Mirrors the provider's default keep_alive.
_WARM_KEEP_ALIVE = "30m"


class AdminUnavailable(RuntimeError):
    """The native Ollama admin API is unreachable or absent (e.g. a non-Ollama backend)."""


@dataclass(frozen=True, slots=True)
class InstalledModel:
    """A model present on disk (from ``/api/tags``)."""

    name: str
    size: int = 0  # bytes on disk
    quantization: str = ""

    @property
    def size_gb(self) -> float:
        return self.size / 1e9


@dataclass(frozen=True, slots=True)
class LoadedModel:
    """A model currently resident (from ``/api/ps``)."""

    name: str
    size_vram: int = 0  # bytes resident in VRAM (0 means fully CPU-offloaded)


def native_root(base_url: str) -> str:
    """Derive the native API root from an OpenAI-compat base_url by stripping a trailing ``/v1``.

    ``http://localhost:11434/v1`` -> ``http://localhost:11434``. A base_url without ``/v1`` is
    returned unchanged; its ``/api/*`` calls will 404 and surface as :class:`AdminUnavailable`.
    """
    url = base_url.rstrip("/")
    if url.endswith("/v1"):
        url = url[: -len("/v1")]
    return url.rstrip("/")


class OllamaAdmin:
    """Thin async client for Ollama's native ``/api/*`` residency endpoints."""

    def __init__(
        self,
        *,
        native_url: str,
        timeout: float = 10.0,
        load_timeout: float = 600.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.native_url = native_url.rstrip("/")
        self.timeout = timeout  # quick metadata calls (tags/ps)
        self.load_timeout = load_timeout  # generate calls: a 14B cold-load can take many seconds
        self._transport = transport

    @classmethod
    def from_config(
        cls, config: Config, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> OllamaAdmin:
        return cls(native_url=native_root(config.base_url), transport=transport)

    # --- HTTP plumbing ----------------------------------------------------
    def _client(self, timeout: float) -> httpx.AsyncClient:
        kwargs: dict[str, Any] = {"timeout": httpx.Timeout(timeout, connect=5.0)}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.AsyncClient(**kwargs)

    async def _request(
        self, method: str, path: str, *, timeout: float, json: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """One admin call; any failure (connect error, 404, other 4xx/5xx) -> AdminUnavailable."""
        url = f"{self.native_url}{path}"
        try:
            async with self._client(timeout) as client:
                resp = await client.request(method, url, json=json)
        except httpx.HTTPError as exc:  # connection refused, timeout, etc.
            raise AdminUnavailable(
                f"Ollama admin API unreachable at {self.native_url}{path}: {exc}"
            ) from exc
        if resp.status_code >= 400:
            # 404 == endpoint absent (non-Ollama backend); anything else is still best-effort.
            raise AdminUnavailable(
                f"Ollama admin API returned HTTP {resp.status_code} for {path}"
            )
        try:
            return resp.json()
        except ValueError:
            return {}

    # --- endpoints --------------------------------------------------------
    async def list_installed(self) -> list[InstalledModel]:
        data = await self._request("GET", "/api/tags", timeout=self.timeout)
        out: list[InstalledModel] = []
        for m in data.get("models") or []:
            details = m.get("details") or {}
            out.append(
                InstalledModel(
                    name=str(m.get("name", "")),
                    size=int(m.get("size", 0) or 0),
                    quantization=str(details.get("quantization_level", "") or ""),
                )
            )
        return [m for m in out if m.name]

    async def list_loaded(self) -> list[LoadedModel]:
        data = await self._request("GET", "/api/ps", timeout=self.timeout)
        out: list[LoadedModel] = []
        for m in data.get("models") or []:
            out.append(
                LoadedModel(
                    name=str(m.get("name", "")),
                    size_vram=int(m.get("size_vram", 0) or 0),
                )
            )
        return [m for m in out if m.name]

    async def unload(self, model: str) -> None:
        """Evict ``model`` from VRAM now (keep_alive=0, no prompt)."""
        await self._request(
            "POST",
            "/api/generate",
            timeout=self.load_timeout,
            json={"model": model, "keep_alive": 0},
        )

    async def warmup(self, model: str, *, num_ctx: int | None = None) -> float:
        """Force ``model`` resident with an empty-prompt load; returns wall-clock load seconds.

        An empty prompt makes Ollama load the weights (and allocate the KV cache for ``num_ctx``)
        without generating — the canonical preload. Passing the same ``num_ctx`` the chat call
        will use means the next completion serves the loaded model instead of reloading it.
        """
        body: dict[str, Any] = {"model": model, "prompt": "", "stream": False,
                                "keep_alive": _WARM_KEEP_ALIVE}
        if num_ctx is not None:
            body["options"] = {"num_ctx": num_ctx}
        start = time.perf_counter()
        await self._request("POST", "/api/generate", timeout=self.load_timeout, json=body)
        return time.perf_counter() - start
