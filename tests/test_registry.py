"""Tests for the tool registry: truncation, dispatch, formatting (TASKS 2.3)."""

from __future__ import annotations

from arescode.config import Config
from arescode.tools.registry import (
    BashAction,
    EditFileAction,
    Executor,
    GrepAction,
    ReadFileAction,
    SearchReplace,
    WriteFileAction,
    action_summary,
    format_observation,
    truncate_output,
)


def test_truncate_short_passthrough():
    text = "a\nb\nc"
    assert truncate_output(text, max_lines=10) == text


def test_truncate_long_elides_middle():
    text = "\n".join(str(i) for i in range(1, 11))  # 10 lines
    out = truncate_output(text, max_lines=4)
    lines = out.splitlines()
    assert lines[0] == "1"
    assert lines[-1] == "10"
    assert any("lines elided" in ln for ln in lines)


def test_action_summary():
    assert action_summary(ReadFileAction("a.py", 10, 5)) == "a.py (offset=10, limit=5)"
    assert action_summary(GrepAction("x", "src", "*.py")) == "/x/ in src glob=*.py"
    assert action_summary(BashAction("ls")) == "ls"


def test_executor_read_file(tmp_path):
    (tmp_path / "a.txt").write_text("hi\n")
    result = Executor(tmp_path, Config()).run(ReadFileAction("a.txt"))
    assert result.ok
    assert result.tool == "read_file"
    assert "hi" in result.output


def test_executor_read_missing_is_error(tmp_path):
    result = Executor(tmp_path, Config()).run(ReadFileAction("nope.txt"))
    assert not result.ok
    assert "not found" in result.output


def test_executor_bash_exit_code(tmp_path):
    result = Executor(tmp_path, Config()).run(BashAction("python -c \"import sys; sys.exit(2)\""))
    assert not result.ok
    assert result.summary == "exit 2"


def test_executor_write_creates_file(tmp_path):
    result = Executor(tmp_path, Config()).run(WriteFileAction("x.py", "print(1)"))
    assert result.ok
    assert result.summary == "created"
    assert (tmp_path / "x.py").read_text() == "print(1)\n"


def test_executor_edit_missing_file_is_error(tmp_path):
    action = EditFileAction("x.py", (SearchReplace("a", "b"),))
    result = Executor(tmp_path, Config()).run(action)
    assert not result.ok
    assert "not found" in result.output


def test_format_observation():
    obs = format_observation(ReadFileAction("a.py"), _ok_result())
    assert obs.startswith("[read_file ok] a.py")
    assert "contents" in obs


def _ok_result():
    from arescode.tools.registry import ToolResult

    return ToolResult("read_file", True, "contents", summary="1 line(s)")
