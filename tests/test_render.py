"""ConsoleObserver rendering: the non-verbose preview that surfaces tool results on screen."""

from __future__ import annotations

import io

from rich.console import Console

from arescode.tools.registry import GlobAction, ReadFileAction, ToolResult
from arescode.ui.render import PREVIEW_LINES, ConsoleObserver


def _observer(*, verbose: bool = False) -> tuple[ConsoleObserver, Console]:
    console = Console(file=io.StringIO(), width=200, force_terminal=False, color_system=None)
    return ConsoleObserver(console, verbose=verbose), console


def test_tool_end_previews_readonly_output() -> None:
    obs, console = _observer()
    result = ToolResult("glob", ok=True, output="a.py\nb.py\nc.py", summary="3 file(s)")
    obs.tool_end(GlobAction(pattern="**/*.py"), result, 0.1)
    out = console.file.getvalue()
    assert "a.py" in out and "b.py" in out and "c.py" in out


def test_tool_end_preview_caps_long_output() -> None:
    obs, console = _observer()
    body = "\n".join(f"file{i}.py" for i in range(PREVIEW_LINES + 5))
    result = ToolResult("glob", ok=True, output=body, summary="many file(s)")
    obs.tool_end(GlobAction(pattern="**/*.py"), result, 0.1)
    out = console.file.getvalue()
    assert "file0.py" in out  # head shown
    assert "more line" in out  # the "+N more line(s)" marker
    assert f"file{PREVIEW_LINES + 4}.py" not in out  # tail beyond the cap is hidden


def test_verbose_shows_full_output_without_preview_marker() -> None:
    obs, console = _observer(verbose=True)
    body = "\n".join(f"file{i}.py" for i in range(PREVIEW_LINES + 5))
    result = ToolResult("glob", ok=True, output=body, summary="many file(s)")
    obs.tool_end(GlobAction(pattern="**/*.py"), result, 0.1)
    out = console.file.getvalue()
    assert f"file{PREVIEW_LINES + 4}.py" in out  # every line present
    assert "more line" not in out  # no preview cap marker in verbose mode


def test_tool_end_previews_error_output() -> None:
    obs, console = _observer()
    result = ToolResult("read_file", ok=False, output="file not found: nope.py", summary="error")
    obs.tool_end(ReadFileAction(path="nope.py"), result, 0.1)
    out = console.file.getvalue()
    assert "file not found" in out  # errors are now visible too, not just summarized
