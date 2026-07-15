"""REPL-level model wiring (D12): startup model activation (incl. --resume restore) and the
``/model <name>`` switch path, driven with a faked admin client (no live Ollama)."""

from __future__ import annotations

import io

from rich.console import Console

from arescode.config import Config, ModelSettings
from arescode.core.models import ModelManager
from arescode.core.state import SessionState
from arescode.providers.ollama_admin import AdminUnavailable, InstalledModel, LoadedModel
from arescode.tools.registry import Executor
from arescode.ui.repl import _activate_model, _switch_model

M7B = "qwen2.5-coder:7b"
M14B = "qwen2.5-coder:14b-instruct"


class FakeAdmin:
    def __init__(self, *, available: bool = True, installed=None) -> None:
        self.available = available
        self._installed = installed or [InstalledModel(M7B), InstalledModel(M14B)]
        self.calls: list[tuple[str, str]] = []

    def _guard(self) -> None:
        if not self.available:
            raise AdminUnavailable("offline")

    async def list_installed(self):
        self._guard()
        return list(self._installed)

    async def list_loaded(self):
        self._guard()
        return [LoadedModel(M7B)]

    async def unload(self, model):
        self._guard()
        self.calls.append(("unload", model))

    async def warmup(self, model, *, num_ctx=None):
        self._guard()
        self.calls.append(("warmup", model))
        return 0.5


def _config() -> Config:
    return Config(model=M7B, num_ctx=16384,
                  models={M7B: ModelSettings(num_ctx=16384), M14B: ModelSettings(num_ctx=8192)})


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False)


def _manager(admin, executor=None) -> ModelManager:
    return ModelManager(_config(), admin, executor=executor)


# --- startup activation / --resume restore ---------------------------------


async def test_activate_applies_per_model_settings_on_fresh_start():
    manager = _manager(FakeAdmin())
    state = SessionState.new(M14B)
    cfg = await _activate_model(manager, state, _config(), _console(), resume=False)
    assert cfg.model == M14B
    assert cfg.num_ctx == 8192  # 14B's per-model window, not the 16384 default


async def test_resume_keeps_installed_model():
    manager = _manager(FakeAdmin())
    state = SessionState.new(M14B)  # as if restored from a saved session
    cfg = await _activate_model(manager, state, _config(), _console(), resume=True)
    assert cfg.model == M14B and cfg.num_ctx == 8192
    assert state.model == M14B


async def test_resume_falls_back_when_model_uninstalled():
    manager = _manager(FakeAdmin())
    state = SessionState.new("mistral:removed")  # no longer installed
    cfg = await _activate_model(manager, state, _config(), _console(), resume=True)
    assert cfg.model == M7B  # fell back to the configured default
    assert state.model == M7B


async def test_resume_with_admin_offline_keeps_recorded_model():
    manager = _manager(FakeAdmin(available=False))
    state = SessionState.new("some:tag")
    cfg = await _activate_model(manager, state, _config(), _console(), resume=True)
    # Can't validate without the admin API, so trust the recorded model rather than crash.
    assert state.model == "some:tag"
    assert cfg.model == "some:tag"


# --- /model <name> switch path ---------------------------------------------


async def test_switch_model_command_switches_and_rebuilds_provider(tmp_path):
    admin = FakeAdmin()
    executor = Executor(tmp_path, _config())
    manager = _manager(admin, executor=executor)
    state = SessionState.new(M7B)
    from arescode.providers.openai_compat import OpenAICompatProvider

    provider = OpenAICompatProvider.from_config(_config())
    cfg, new_provider = await _switch_model(
        manager=manager, state=state, config=_config(), provider=provider,
        console=_console(), target="qwen2.5-coder:14b",  # prefix, resolves to 14b-instruct
    )
    assert state.model == M14B
    assert cfg.num_ctx == 8192
    assert new_provider.model == M14B
    assert admin.calls == [("unload", M7B), ("warmup", M14B)]
    assert executor.active_model == M14B


async def test_switch_model_ambiguous_target_is_rejected(tmp_path):
    admin = FakeAdmin()
    manager = _manager(admin)
    state = SessionState.new(M7B)
    from arescode.providers.openai_compat import OpenAICompatProvider

    provider = OpenAICompatProvider.from_config(_config())
    cfg, new_provider = await _switch_model(
        manager=manager, state=state, config=_config(), provider=provider,
        console=_console(), target="qwen2.5-coder",  # prefixes both -> ambiguous
    )
    assert state.model == M7B  # unchanged
    assert admin.calls == []
    assert new_provider is provider  # provider not rebuilt
