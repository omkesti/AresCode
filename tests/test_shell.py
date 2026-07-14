"""Tests for the sandboxed bash tool (TASKS 2.5)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from arescode.tools.base import ToolError
from arescode.tools.shell import run_bash


def test_echo_succeeds(tmp_path):
    res = run_bash("echo hello", tmp_path)
    assert res.exit_code == 0
    assert "hello" in res.output


def test_nonzero_exit_code(tmp_path):
    res = run_bash("python -c \"import sys; sys.exit(3)\"", tmp_path)
    assert res.exit_code == 3


def test_cwd_is_locked_to_project(tmp_path):
    res = run_bash("python -c \"import os; print(os.getcwd())\"", tmp_path)
    assert Path(res.output.strip()) == tmp_path.resolve()


def test_timeout_kills_and_reports(tmp_path):
    res = run_bash(f'"{sys.executable}" -c "import time; time.sleep(5)"', tmp_path, timeout=1)
    assert res.timed_out is True
    assert res.exit_code == 124


def test_blocked_command_raises(tmp_path):
    with pytest.raises(ToolError, match="blocked"):
        run_bash("rm -rf /", tmp_path)


def test_interactive_command_rejected(tmp_path):
    with pytest.raises(ToolError, match="interactive"):
        run_bash("vim notes.txt", tmp_path)


def test_empty_command_raises(tmp_path):
    with pytest.raises(ToolError, match="empty"):
        run_bash("   ", tmp_path)
