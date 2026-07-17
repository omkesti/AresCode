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


# --- per-model edit telemetry (D12) ----------------------------------------


def test_edit_telemetry_is_tagged_per_active_model(tmp_path):
    (tmp_path / "m.py").write_text("value = 1\n")
    ex = Executor(tmp_path, Config())
    ex.active_model = "model-a"
    assert ex.run(EditFileAction("m.py", (SearchReplace("value = 1", "value = 2"),))).ok

    ex.active_model = "model-b"
    assert ex.run(EditFileAction("m.py", (SearchReplace("value = 2", "value = 3"),))).ok

    # Each edit is attributed to the model that was active when it ran.
    assert ex.stats_by_model["model-a"].exact == 1
    assert ex.stats_by_model["model-b"].exact == 1
    # The rolled-up total still reflects both.
    assert ex.stats.exact == 2
    report = ex.stats_report()
    assert "[model-a]" in report and "[model-b]" in report and "[all]" in report


def test_active_model_defaults_to_config_model(tmp_path):
    ex = Executor(tmp_path, Config(model="from-config"))
    assert ex.active_model == "from-config"


# --- fs_generation: the "tree may have changed" signal the REPL watches --------------------


def test_fs_generation_bumps_on_write_and_edit(tmp_path):
    ex = Executor(tmp_path, Config())
    assert ex.fs_generation == 0
    assert ex.run(WriteFileAction("x.py", "value = 1\n")).ok
    assert ex.fs_generation == 1  # new file -> tree changed
    assert ex.run(EditFileAction("x.py", (SearchReplace("value = 1", "value = 2"),))).ok
    assert ex.fs_generation == 2  # successful edit -> tree changed


def test_fs_generation_bumps_on_bash(tmp_path):
    ex = Executor(tmp_path, Config())
    ex.run(BashAction("python -c \"pass\""))
    assert ex.fs_generation == 1  # bash may have touched the tree; flagged conservatively


def test_fs_generation_steady_on_read_only_tools(tmp_path):
    (tmp_path / "a.txt").write_text("hi\n")
    ex = Executor(tmp_path, Config())
    ex.run(ReadFileAction("a.txt"))
    ex.run(GrepAction("hi"))
    assert ex.fs_generation == 0  # reads/searches never change the tree


def test_fs_generation_steady_on_failed_write_and_edit(tmp_path):
    (tmp_path / "exists.py").write_text("keep\n")
    ex = Executor(tmp_path, Config())
    # write_file refuses to overwrite an existing file -> no change.
    assert not ex.run(WriteFileAction("exists.py", "new")).ok
    # edit_file against a missing file -> no change.
    assert not ex.run(EditFileAction("missing.py", (SearchReplace("a", "b"),))).ok
    assert ex.fs_generation == 0
