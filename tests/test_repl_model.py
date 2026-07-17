"""REPL-level model wiring (D12): startup model activation (incl. --resume restore) and the
``/model <name>`` switch path, driven with a faked admin client (no live Ollama)."""

from __future__ import annotations

import io

from rich.console import Console

from arescode import config as config_module
from arescode.config import Config, ModelSettings
from arescode.core.models import ModelManager
from arescode.core.state import SessionState
from arescode.providers.ollama_admin import (
    AdminUnavailable,
    InstalledModel,
    LoadedModel,
    ModelLoadError,
)
from arescode.tools.registry import Executor
from arescode.ui import repl as repl_mod
from arescode.ui.repl import _activate_model, _preflight, _switch_model

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


# --- startup self-heal of a poisoned remembered default (D13) --------------


async def test_activate_self_heals_poisoned_remembered_default():
    # last_model points at 14b, which crashes on load -> fall back to 7b AND forget the poison.
    config_module.save_last_model(M14B)
    admin = FakeAdmin()

    async def crash(model, *, num_ctx=None):
        raise ModelLoadError("CUDA error: shared object initialization failed")

    admin.warmup = crash  # type: ignore[assignment]
    manager = _manager(admin)
    state = SessionState.new(M14B)

    cfg = await _activate_model(manager, state, _config(), _console(), resume=False)

    assert cfg.model == M7B  # fell back to the safe built-in default
    assert state.model == M7B
    assert config_module.read_last_model() == M7B  # remembered default rewritten so it self-heals


async def test_activate_keeps_remembered_default_that_loads():
    config_module.save_last_model(M14B)
    admin = FakeAdmin()  # warmup succeeds
    manager = _manager(admin)
    state = SessionState.new(M14B)

    cfg = await _activate_model(manager, state, _config(), _console(), resume=False)

    assert cfg.model == M14B and cfg.num_ctx == 8192
    assert ("warmup", M14B) in admin.calls  # it was verified to load
    assert config_module.read_last_model() == M14B  # left untouched


async def test_activate_does_not_verify_a_non_remembered_model():
    # No remembered default -> the active model came from config/default/session, not a /model
    # switch; there is nothing to self-heal, so skip the eager verification entirely.
    admin = FakeAdmin()
    manager = _manager(admin)
    state = SessionState.new(M14B)

    await _activate_model(manager, state, _config(), _console(), resume=False)

    assert admin.calls == []  # never warmed up


async def test_activate_does_not_verify_when_remembered_is_the_safe_default():
    # If the remembered default already is the safe harbor, there's nothing safer to fall back to.
    config_module.save_last_model(M7B)
    admin = FakeAdmin()
    manager = _manager(admin)
    state = SessionState.new(M7B)

    await _activate_model(manager, state, _config(), _console(), resume=False)

    assert admin.calls == []


async def test_activate_trusts_remembered_model_when_admin_offline():
    # Can't verify without the native API -> keep the remembered model, load lazily as before.
    config_module.save_last_model(M14B)
    manager = _manager(FakeAdmin(available=False))
    state = SessionState.new(M14B)

    cfg = await _activate_model(manager, state, _config(), _console(), resume=False)

    assert cfg.model == M14B
    assert config_module.read_last_model() == M14B  # not forgotten on an unverifiable launch


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
    # D13: a successful switch is remembered as the next launch's default (isolated by conftest).
    assert config_module.read_last_model() == M14B


# --- first-run preflight (TASKS 6.2) ---------------------------------------


def _force_rg(monkeypatch, present: bool) -> None:
    """Pin ripgrep presence so preflight tests don't depend on the host's PATH."""
    monkeypatch.setattr(repl_mod.shutil, "which", lambda name: "/usr/bin/rg" if present else None)


async def test_preflight_quiet_when_server_up_and_model_present(monkeypatch):
    _force_rg(monkeypatch, True)
    console = _console()
    await _preflight(_manager(FakeAdmin()), M7B, "http://localhost:11434/v1", console)
    assert console.file.getvalue() == ""  # nothing to warn about


async def test_preflight_warns_when_model_missing(monkeypatch):
    _force_rg(monkeypatch, True)
    console = _console()
    manager = _manager(FakeAdmin(installed=[InstalledModel(M14B)]))
    await _preflight(manager, M7B, "http://localhost:11434/v1", console)
    assert f"ollama pull {M7B}" in console.file.getvalue()


async def test_preflight_warns_when_server_down(monkeypatch):
    _force_rg(monkeypatch, True)
    console = _console()
    manager = _manager(FakeAdmin(available=False))
    await _preflight(manager, M7B, "http://localhost:11434/v1", console)
    assert "ollama serve" in console.file.getvalue()


async def test_preflight_quiet_for_non_ollama_backend(monkeypatch):
    _force_rg(monkeypatch, True)
    console = _console()

    class Admin404:
        async def list_installed(self):
            raise AdminUnavailable("no native API here", status=404)

    await _preflight(_manager(Admin404()), M7B, "http://localhost:1234/v1", console)
    assert console.file.getvalue() == ""  # can't probe a non-Ollama backend -> stay silent


async def test_preflight_reports_missing_ripgrep(monkeypatch):
    _force_rg(monkeypatch, False)
    console = _console()
    await _preflight(_manager(FakeAdmin()), M7B, "http://localhost:11434/v1", console)
    assert "ripgrep" in console.file.getvalue()


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
    assert config_module.read_last_model() is None  # D13: a rejected switch is not remembered
