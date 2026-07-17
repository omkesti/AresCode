"""Tests for REPL slash-command parsing (TASKS 1.6)."""

from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace

import pytest
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.keys import Keys
from rich.console import Console

import arescode.ui.repl as repl_mod
from arescode.core.state import SessionState
from arescode.ui.repl import (
    _await_turn,
    _build_key_bindings,
    _build_prompt,
    _map_structure,
    parse_command,
)


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False)


def test_exit_and_quit():
    state = SessionState.new("m")
    assert parse_command("/exit", state, _console()).action == "exit"
    assert parse_command("/quit", state, _console()).action == "exit"


def test_clear_empties_history():
    state = SessionState.new("m")
    state.user("x")
    command = parse_command("/clear", state, _console())
    assert command.action == "continue"
    assert state.messages == []


def test_model_with_name_sets_target():
    # parse_command only signals intent now; the async switch (validate/unload/warmup) runs in the
    # REPL, so state.model is unchanged here and the target is carried on the command.
    state = SessionState.new("m")
    command = parse_command("/model llama3", state, _console())
    assert command.model_target == "llama3"
    assert command.model_pick is False
    assert state.model == "m"  # not mutated until the switch actually completes


def test_model_without_arg_requests_picker():
    state = SessionState.new("m")
    command = parse_command("/model", state, _console())
    assert command.model_pick is True
    assert command.model_target is None
    assert command.action == "continue"
    assert state.model == "m"


def test_help_and_unknown_continue():
    state = SessionState.new("m")
    assert parse_command("/help", state, _console()).action == "continue"
    assert parse_command("/bogus", state, _console()).action == "continue"


def test_init_command_requests_authoring_turn():
    command = parse_command("/init", SessionState.new("m"), _console())
    assert command.init is True
    assert command.action == "continue"


def test_enter_submits_and_newline_keys_are_bound():
    # Guards the fix for the "Enter does nothing" freeze: Enter must submit, not newline.
    bindings = {b.keys: b.handler.__name__ for b in _build_key_bindings().bindings}
    assert bindings[(Keys.ControlM,)] == "_submit"  # Enter -> send
    assert bindings[(Keys.ControlJ,)] == "_newline"  # Ctrl+J -> newline
    assert bindings[(Keys.Escape, Keys.ControlM)] == "_newline"  # Alt+Enter -> newline


def _enter_handler():
    for b in _build_key_bindings().bindings:
        if b.keys == (Keys.ControlM,):
            return b.handler
    raise AssertionError("Enter is not bound")


def test_trailing_backslash_enter_inserts_newline():
    # `\` + Enter is line continuation: the backslash is consumed and a newline inserted,
    # instead of submitting the message.
    buffer = Buffer()
    buffer.insert_text("first\\")
    submitted = False

    def _fail_submit() -> None:
        nonlocal submitted
        submitted = True

    buffer.validate_and_handle = _fail_submit  # type: ignore[method-assign]
    _enter_handler()(SimpleNamespace(current_buffer=buffer))

    assert buffer.text == "first\n"  # backslash gone, newline added
    assert submitted is False  # did not send


def test_enter_without_backslash_submits():
    buffer = Buffer()
    buffer.insert_text("send me")
    submitted = False

    def _record_submit() -> None:
        nonlocal submitted
        submitted = True

    buffer.validate_and_handle = _record_submit  # type: ignore[method-assign]
    _enter_handler()(SimpleNamespace(current_buffer=buffer))

    assert submitted is True
    assert buffer.text == "send me"  # unchanged


def test_verbose_command_toggles():
    state = SessionState.new("m")
    command = parse_command("/verbose", state, _console())
    assert command.action == "continue"
    assert command.toggle_verbose is True


def test_map_command_requests_map():
    command = parse_command("/map", SessionState.new("m"), _console())
    assert command.show_map is True


def test_compact_command_requests_compaction():
    command = parse_command("/compact", SessionState.new("m"), _console())
    assert command.compact is True


def test_allow_without_arg_requests_listing():
    command = parse_command("/allow", SessionState.new("m"), _console())
    assert command.show_allow is True
    assert command.allow is None


def test_allow_with_token_sets_allow():
    command = parse_command("/allow pytest -q", SessionState.new("m"), _console())
    assert command.allow == "pytest"  # only the first token is allowlisted


def test_deny_with_token_sets_deny():
    command = parse_command("/deny pytest", SessionState.new("m"), _console())
    assert command.deny == "pytest"


def test_deny_without_arg_is_noop():
    command = parse_command("/deny", SessionState.new("m"), _console())
    assert command.action == "continue"
    assert command.deny is None


async def _never_escape() -> None:
    await asyncio.Event().wait()


async def test_await_turn_returns_done_when_turn_finishes(monkeypatch):
    # No Esc -> the turn runs to completion and its result is surfaced.
    monkeypatch.setattr(repl_mod, "_watch_for_escape", _never_escape)

    async def _turn() -> str:
        return "answer"

    task = asyncio.ensure_future(_turn())
    assert await _await_turn(task) == "done"
    assert task.result() == "answer"


async def test_await_turn_propagates_turn_error(monkeypatch):
    # A turn that raises must surface its exception, not be swallowed as an escape.
    monkeypatch.setattr(repl_mod, "_watch_for_escape", _never_escape)

    async def _boom() -> None:
        raise RuntimeError("kaboom")

    task = asyncio.ensure_future(_boom())
    with pytest.raises(RuntimeError, match="kaboom"):
        await _await_turn(task)


async def test_await_turn_escapes_and_cancels_on_esc(monkeypatch):
    # Esc fires first -> the in-flight turn is cancelled and control returns immediately.
    async def _escape_now() -> None:
        return

    monkeypatch.setattr(repl_mod, "_watch_for_escape", _escape_now)

    async def _long_turn() -> None:
        await asyncio.sleep(60)

    task = asyncio.ensure_future(_long_turn())
    assert await _await_turn(task) == "escaped"
    assert task.cancelled()


# --- repo-map refresh helpers (mid-session staleness fix) ---------------------------------


def test_map_structure_ignores_size_churn():
    # Editing a file changes its rendered size but not the project's shape -> same structure.
    before = "core/\n  loop.py  1.2K\nmain.py  800B"
    after = "core/\n  loop.py  3.4K\nmain.py  810B"
    assert _map_structure(before) == _map_structure(after)


def test_map_structure_detects_added_and_removed_files():
    base = "main.py  800B"
    assert _map_structure(base) != _map_structure(base + "\nextra.py  10B")  # added
    assert _map_structure(base + "\nextra.py  10B") != _map_structure(base)  # removed


def test_build_prompt_reflects_current_tree_and_ares_memory(tmp_path):
    (tmp_path / "hello.py").write_text("print('hi')\n")
    (tmp_path / "ARES.md").write_text("# Memory\nProject-specific note.\n")
    repo_map, system_prompt = _build_prompt(tmp_path)
    assert "hello.py" in repo_map  # freshly scanned from disk
    assert "hello.py" in system_prompt  # the map is embedded in the prompt
    assert "Project-specific note." in system_prompt  # ARES.md memory is injected

    # A file added after the first build shows up on the next build (no relaunch needed).
    (tmp_path / "world.py").write_text("print('bye')\n")
    repo_map_2, _ = _build_prompt(tmp_path)
    assert "world.py" in repo_map_2
    assert _map_structure(repo_map) != _map_structure(repo_map_2)
