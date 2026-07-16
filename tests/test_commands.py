"""Tests for REPL slash-command parsing (TASKS 1.6)."""

from __future__ import annotations

import io

from prompt_toolkit.keys import Keys
from rich.console import Console

from arescode.core.state import SessionState
from arescode.ui.repl import _build_key_bindings, parse_command


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


def test_enter_submits_and_newline_keys_are_bound():
    # Guards the fix for the "Enter does nothing" freeze: Enter must submit, not newline.
    bindings = {b.keys: b.handler.__name__ for b in _build_key_bindings().bindings}
    assert bindings[(Keys.ControlM,)] == "_submit"  # Enter -> send
    assert bindings[(Keys.ControlJ,)] == "_newline"  # Ctrl+J -> newline
    assert bindings[(Keys.Escape, Keys.ControlM)] == "_newline"  # Alt+Enter -> newline


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
