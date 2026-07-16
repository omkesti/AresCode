"""Tests for the ModelManager switch lifecycle and per-model config resolution (D12).

The admin client is faked (records call order + toggles availability), so no live Ollama is needed.
Covers acceptance check #1: unload-before-warmup, budget recompute + compaction on a shrinking
window, graceful degrade when the admin API is unavailable, and mid-turn rejection.
"""

from __future__ import annotations

import pytest

from arescode.config import Config, ModelSettings
from arescode.core.models import (
    ModelManager,
    budget_for,
    estimate_tokens,
    hard_truncate,
    match_model,
)
from arescode.core.state import SessionState
from arescode.providers.ollama_admin import (
    AdminUnavailable,
    InstalledModel,
    LoadedModel,
    ModelLoadError,
)
from arescode.tools.registry import Executor

M7B = "qwen2.5-coder:7b"
M14B = "qwen2.5-coder:14b-instruct"


class FakeAdmin:
    """Stand-in for OllamaAdmin: records unload/warmup order; can play unavailable."""

    def __init__(self, *, available: bool = True, installed=None, loaded=None) -> None:
        self.available = available
        self._installed = installed or [
            InstalledModel(M7B, 4_700_000_000, "Q4_K_M"),
            InstalledModel(M14B, 8_988_000_000, "Q4_K_M"),
        ]
        self._loaded = set(loaded or [M7B])
        self.calls: list[tuple[str, str]] = []

    def _guard(self) -> None:
        if not self.available:
            raise AdminUnavailable("offline")

    async def list_installed(self):
        self._guard()
        return list(self._installed)

    async def list_loaded(self):
        self._guard()
        return [LoadedModel(n) for n in self._loaded]

    async def unload(self, model: str) -> None:
        self._guard()
        self.calls.append(("unload", model))
        self._loaded.discard(model)

    async def warmup(self, model: str, *, num_ctx=None) -> float:
        self._guard()
        self.calls.append(("warmup", model))
        self._loaded.add(model)
        return 1.23


def _config() -> Config:
    return Config(
        model=M7B,
        num_ctx=16384,
        temperature=0.1,
        models={
            M7B: ModelSettings(num_ctx=16384),
            M14B: ModelSettings(num_ctx=8192),
        },
    )


def _state(model: str, *, big_messages: int = 0, msg_chars: int = 4000) -> SessionState:
    state = SessionState.new(model)
    for i in range(big_messages):
        state.append("user" if i % 2 == 0 else "assistant", "x" * msg_chars)
    return state


# --- per-model config resolution -------------------------------------------


def test_settings_for_uses_section_then_defaults():
    cfg = _config()
    assert cfg.settings_for(M14B) == (8192, 0.1)  # section wins
    assert cfg.settings_for("some:unknown") == (16384, 0.1)  # falls back to defaults


def test_for_model_applies_num_ctx_and_model():
    eff = _config().for_model(M14B)
    assert eff.model == M14B
    assert eff.num_ctx == 8192
    assert eff.temperature == 0.1


def test_partial_section_inherits_default_temperature():
    cfg = Config(num_ctx=16384, temperature=0.2, models={M14B: ModelSettings(num_ctx=8192)})
    assert cfg.settings_for(M14B) == (8192, 0.2)  # temperature inherited


# --- match_model -----------------------------------------------------------


def test_match_exact_and_prefix():
    installed = [M7B, M14B]
    assert match_model(M7B, installed).model == M7B
    assert match_model("qwen2.5-coder:14b", installed).model == M14B  # unique prefix


def test_match_ambiguous_and_missing():
    installed = [M7B, M14B]
    ambiguous = match_model("qwen2.5-coder", installed)  # prefixes both
    assert ambiguous.model is None and "ambiguous" in ambiguous.error
    missing = match_model("llama3", installed)
    assert missing.model is None and "no installed model" in missing.error


# --- token budget helpers --------------------------------------------------


def test_budget_and_estimate():
    assert budget_for(16384) == 16384 - 1500
    state = _state(M7B, big_messages=2, msg_chars=400)
    assert estimate_tokens(state.messages) == 2 * (400 // 4)


def test_hard_truncate_drops_oldest_until_fit():
    state = _state(M7B, big_messages=10, msg_chars=4000)  # 10 * 1000 = 10_000 tokens
    removed = hard_truncate(state.messages, budget=2000, keep_last=4)
    assert removed > 0
    assert estimate_tokens(state.messages) <= 2000 or len(state.messages) == 4


# --- switch lifecycle ------------------------------------------------------


async def test_switch_unloads_current_before_warming_target():
    admin = FakeAdmin()
    manager = ModelManager(_config(), admin)
    state = _state(M7B)

    result = await manager.switch(state, M14B)

    assert result.ok
    assert admin.calls == [("unload", M7B), ("warmup", M14B)]  # order matters
    assert result.num_ctx == 8192
    assert result.load_seconds == pytest.approx(1.23)


async def test_switch_updates_state_and_appends_note():
    admin = FakeAdmin()
    manager = ModelManager(_config(), admin)
    state = _state(M7B)

    await manager.switch(state, M14B)

    assert state.model == M14B
    notes = [m.content for m in state.messages if m.role == "system"]
    assert any("model switched to qwen2.5-coder:14b-instruct" in n for n in notes)


async def test_switch_tags_executor_active_model(tmp_path):
    admin = FakeAdmin()
    executor = Executor(tmp_path, _config())
    manager = ModelManager(_config(), admin, executor=executor)

    await manager.switch(_state(M7B), M14B)

    assert executor.active_model == M14B


async def test_switch_shrinking_window_triggers_compaction():
    # History fits a 16k window but not an 8k one -> switching 7b(16k) -> 14b(8k) must compact.
    admin = FakeAdmin()
    manager = ModelManager(_config(), admin)
    state = _state(M7B, big_messages=8, msg_chars=4000)  # ~8000 tokens
    assert estimate_tokens(state.messages) < budget_for(16384)  # fits the old window
    assert estimate_tokens(state.messages) > budget_for(8192)  # overflows the new window

    result = await manager.switch(state, M14B)

    assert result.ok
    assert result.removed_messages > 0
    assert any("dropped the" in w for w in result.warnings)
    assert estimate_tokens(state.messages) <= budget_for(8192) or len(state.messages) == 4


async def test_switch_growing_window_does_not_compact():
    admin = FakeAdmin(loaded={M14B})
    manager = ModelManager(_config(), admin)
    state = _state(M14B, big_messages=4, msg_chars=2000)  # small; fits both windows

    result = await manager.switch(state, M7B)  # 8k -> 16k

    assert result.ok
    assert result.removed_messages == 0


async def test_switch_rejects_uninstalled_target():
    admin = FakeAdmin()
    manager = ModelManager(_config(), admin)
    state = _state(M7B)

    result = await manager.switch(state, "mistral:latest")

    assert not result.ok
    assert "not installed" in result.error
    assert admin.calls == []  # nothing unloaded/warmed
    assert state.model == M7B  # unchanged


async def test_switch_degrades_when_admin_unavailable():
    admin = FakeAdmin(available=False)
    manager = ModelManager(_config(), admin)
    state = _state(M7B)

    result = await manager.switch(state, M14B)

    assert result.ok
    assert result.degraded
    assert admin.calls == []  # no native calls attempted
    assert state.model == M14B  # name-only switch still happens


async def test_switch_rejected_mid_turn():
    admin = FakeAdmin()
    manager = ModelManager(_config(), admin)
    manager.busy = True
    state = _state(M7B)

    result = await manager.switch(state, M14B)

    assert not result.ok
    assert "mid-task" in result.error
    assert admin.calls == []
    assert state.model == M7B


async def test_switch_aborts_and_stays_when_target_fails_to_load():
    # A ModelLoadError from warmup (e.g. the target is too large for VRAM) must fail the switch and
    # leave the working model active, so the REPL never persists a model that can't run (D13).
    admin = FakeAdmin()

    async def crash(model: str, *, num_ctx=None):
        raise ModelLoadError(f"{model} failed to load: CUDA error: shared object init failed")

    admin.warmup = crash  # type: ignore[assignment]
    manager = ModelManager(_config(), admin)
    state = _state(M7B)

    result = await manager.switch(state, M14B)

    assert not result.ok
    assert "failed to load" in result.error
    assert "staying on qwen2.5-coder:7b" in result.error
    assert state.model == M7B  # unchanged — the working model stays active
    notes = [m.content for m in state.messages if m.role == "system"]
    assert not any("model switched" in n for n in notes)  # no switch note appended


async def test_verify_loads_propagates_model_load_error():
    admin = FakeAdmin()

    async def crash(model: str, *, num_ctx=None):
        raise ModelLoadError("boom")

    admin.warmup = crash  # type: ignore[assignment]
    manager = ModelManager(_config(), admin)

    with pytest.raises(ModelLoadError):
        await manager.verify_loads(M14B)


async def test_unload_failure_never_aborts_switch():
    admin = FakeAdmin()

    async def boom(model):  # unload blows up
        raise RuntimeError("VRAM gremlins")

    admin.unload = boom  # type: ignore[assignment]
    manager = ModelManager(_config(), admin)
    state = _state(M7B)

    result = await manager.switch(state, M14B)

    assert result.ok  # switch proceeds despite the unload failure
    assert state.model == M14B
    assert any("unload of" in w for w in result.warnings)
    assert ("warmup", M14B) in admin.calls  # still warmed the target
