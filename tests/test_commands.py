"""Tests for REPL slash-command parsing (TASKS 1.6)."""

from __future__ import annotations

import io

from rich.console import Console

from agentcli.core.state import SessionState
from agentcli.ui.repl import parse_command


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


def test_model_switch_updates_state():
    state = SessionState.new("m")
    command = parse_command("/model llama3", state, _console())
    assert command.model == "llama3"
    assert state.model == "llama3"


def test_model_without_arg_reports_current():
    state = SessionState.new("m")
    command = parse_command("/model", state, _console())
    assert command.model is None
    assert command.action == "continue"
    assert state.model == "m"


def test_help_and_unknown_continue():
    state = SessionState.new("m")
    assert parse_command("/help", state, _console()).action == "continue"
    assert parse_command("/bogus", state, _console()).action == "continue"
