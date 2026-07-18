"""ConsoleObserver rendering: the non-verbose preview that surfaces tool results on screen."""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from arescode.tools.registry import GlobAction, ReadFileAction, ToolResult
from arescode.ui.render import PREVIEW_LINES, ConsoleObserver, cleared, force_utf8


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


def _cp1252_console() -> Console:
    """A console whose stdout is a redirected cp1252 pipe (a headless Windows dogfood run)."""
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    return Console(file=stream, width=100, force_terminal=False, color_system=None)


def test_cp1252_stdout_crashes_without_the_fix() -> None:
    # Locks in that the trace glyph (●) is genuinely un-encodable on cp1252, so the guard below
    # is testing something real (dogfood Finding B). If this ever stops raising, the regression
    # test's premise is gone.
    obs = ConsoleObserver(_cp1252_console())
    with pytest.raises(UnicodeEncodeError):
        obs.tool_start(ReadFileAction(path="x.py"))


def test_force_utf8_lets_the_trace_survive_cp1252_stdout() -> None:
    # The headless repro the dogfood run lacked: with force_utf8 applied, the ● trace line and a
    # diff carrying non-ASCII glyphs render to a cp1252 pipe without raising (Finding B fix).
    console = _cp1252_console()
    force_utf8(console.file)
    obs = ConsoleObserver(console)
    obs.tool_start(ReadFileAction(path="x.py"))
    obs._render_diff("--- a\n+++ b\n-old ●\n+new ●\n")
    console.file.flush()
    assert "●".encode() in console.file.buffer.getvalue()  # ● written as UTF-8 bytes


def test_force_utf8_is_a_noop_on_streams_without_reconfigure() -> None:
    # StringIO has no reconfigure(); force_utf8 must tolerate it rather than raise (the guard
    # path used by the existing StringIO-backed tests and any non-text sink).
    force_utf8(io.StringIO())  # no exception


def test_cleared_shows_only_the_wordmark_and_a_notice() -> None:
    # /clear wipes the screen and leaves the wordmark plus a cleared notice (UX task 4).
    console = Console(file=io.StringIO(), width=100, force_terminal=False, color_system=None)
    cleared(console)
    out = console.file.getvalue()
    assert "█" in out  # the wordmark rendered
    assert "cleared" in out  # the conversation-cleared notice
    assert "Welcome to AresCode" not in out  # only the wordmark, not the full banner
