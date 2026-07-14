"""Tool registry: action dataclasses, result formatting/truncation, and the executor.

The parser produces the ``Action`` dataclasses defined here; the :class:`Executor` dispatches
each one to its implementation and wraps the outcome in a :class:`ToolResult`. Results are
truncated (~200 lines, head + tail) before they re-enter the message history
(context.md §4.4, TASKS 2.3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from arescode.tools.base import ToolError
from arescode.tools.files import read_file
from arescode.tools.search import glob_files, grep
from arescode.tools.shell import run_bash

if TYPE_CHECKING:
    from arescode.config import Config

# ---------------------------------------------------------------------------
# Actions (frozen + hashable so the loop can detect identical consecutive actions)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SearchReplace:
    search: str
    replace: str


@dataclass(frozen=True, slots=True)
class ReadFileAction:
    tool: ClassVar[str] = "read_file"
    path: str
    offset: int | None = None
    limit: int | None = None


@dataclass(frozen=True, slots=True)
class GrepAction:
    tool: ClassVar[str] = "grep"
    pattern: str
    path: str | None = None
    glob: str | None = None


@dataclass(frozen=True, slots=True)
class GlobAction:
    tool: ClassVar[str] = "glob"
    pattern: str


@dataclass(frozen=True, slots=True)
class BashAction:
    tool: ClassVar[str] = "bash"
    cmd: str


@dataclass(frozen=True, slots=True)
class WriteFileAction:
    tool: ClassVar[str] = "write_file"
    path: str
    content: str


@dataclass(frozen=True, slots=True)
class EditFileAction:
    tool: ClassVar[str] = "edit_file"
    path: str
    edits: tuple[SearchReplace, ...]


Action = (
    ReadFileAction | GrepAction | GlobAction | BashAction | WriteFileAction | EditFileAction
)


def action_summary(action: Action) -> str:
    """A short one-line description of an action's arguments (for the trace UI)."""
    if isinstance(action, ReadFileAction):
        extra = ""
        if action.offset or action.limit:
            extra = f" (offset={action.offset}, limit={action.limit})"
        return f"{action.path}{extra}"
    if isinstance(action, GrepAction):
        scope = f" in {action.path}" if action.path else ""
        scope += f" glob={action.glob}" if action.glob else ""
        return f"/{action.pattern}/{scope}"
    if isinstance(action, GlobAction):
        return action.pattern
    if isinstance(action, BashAction):
        return action.cmd
    if isinstance(action, (WriteFileAction, EditFileAction)):
        return action.path
    return repr(action)


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ToolResult:
    tool: str
    ok: bool
    output: str
    summary: str = ""  # short status for the trace line, e.g. "12 matches" / "exit 0"


def truncate_output(text: str, *, max_lines: int = 200, head: int | None = None) -> str:
    """Clamp long output to ~``max_lines`` lines (head + tail) with an elision marker."""
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    head = head if head is not None else max_lines // 2
    tail = max_lines - head
    omitted = len(lines) - head - tail
    return "\n".join(lines[:head] + [f"... [{omitted} lines elided] ..."] + lines[-tail:])


def format_observation(action: Action, result: ToolResult) -> str:
    """Render a tool result as the text fed back to the model on the next turn."""
    status = "ok" if result.ok else "error"
    header = f"[{result.tool} {status}] {action_summary(action)}"
    return f"{header}\n{result.output}"


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------

_READONLY = (ReadFileAction, GrepAction, GlobAction)


@dataclass(slots=True)
class Executor:
    """Dispatches an action to its tool implementation, within a fixed project dir."""

    project_dir: Path
    config: Config
    result_max_lines: int = field(default=200)

    def is_readonly(self, action: Action) -> bool:
        return isinstance(action, _READONLY)

    def run(self, action: Action) -> ToolResult:
        try:
            return self._dispatch(action)
        except ToolError as exc:
            return ToolResult(getattr(action, "tool", "unknown"), ok=False, output=str(exc),
                              summary="error")

    def _dispatch(self, action: Action) -> ToolResult:
        if isinstance(action, ReadFileAction):
            out = read_file(self.project_dir, action.path, action.offset, action.limit)
            return ToolResult("read_file", True, self._clamp(out), summary=_count_lines(out))
        if isinstance(action, GrepAction):
            out, n = grep(self.project_dir, action.pattern, action.path, action.glob)
            return ToolResult("grep", True, self._clamp(out), summary=f"{n} match(es)")
        if isinstance(action, GlobAction):
            out, n = glob_files(self.project_dir, action.pattern)
            return ToolResult("glob", True, self._clamp(out), summary=f"{n} file(s)")
        if isinstance(action, BashAction):
            res = run_bash(action.cmd, self.project_dir, timeout=self.config.bash_timeout)
            return ToolResult("bash", res.exit_code == 0, self._clamp(res.output),
                              summary=f"exit {res.exit_code}")
        if isinstance(action, (WriteFileAction, EditFileAction)):
            msg = f"{action.tool} is not available yet — the edit path lands in Phase 3."
            return ToolResult(action.tool, False, msg, summary="deferred")
        return ToolResult("unknown", False, f"unknown action: {action!r}", summary="error")

    def _clamp(self, text: str) -> str:
        return truncate_output(text, max_lines=self.result_max_lines)


def _count_lines(text: str) -> str:
    n = len(text.splitlines())
    return f"{n} line(s)"
