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

from arescode.permissions.gate import Decision, Gate
from arescode.tools.base import ToolError
from arescode.tools.edit import EditStats, apply_edit, preview_edit, preview_write, write_new_file
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
    diff: str = ""  # unified diff for edit/write results (rendered by the UI, not sent to model)


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
    """Dispatches an action to its tool implementation, within a fixed project dir.

    The interactive gate (allow / ask / approve) lives in ``core/loop.py``. When a ``gate`` is
    supplied here it is a belt-and-suspenders **hard-deny** check only: a blocklisted command or a
    path escape can never execute, regardless of caller. ASK verdicts pass straight through —
    their approval already happened in the loop before ``run`` was called. With no gate (tests,
    headless runs) every action runs.

    Edit telemetry is kept **per model** (``stats_by_model``, keyed by ``active_model``) so a
    mid-session model switch attributes each edit to the model that produced it (D12); ``stats``
    exposes the rolled-up total for callers that want one number.
    """

    project_dir: Path
    config: Config
    result_max_lines: int = field(default=200)
    gate: Gate | None = None
    active_model: str = ""  # tags the model that produced each edit; set by ModelManager on switch
    stats_by_model: dict[str, EditStats] = field(default_factory=dict)
    _edit_failures: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.active_model:
            self.active_model = self.config.model

    @property
    def stats(self) -> EditStats:
        """Aggregate edit telemetry across every model used this session."""
        return _aggregate_stats(self.stats_by_model)

    def _stats_for(self, model: str) -> EditStats:
        stats = self.stats_by_model.get(model)
        if stats is None:
            stats = EditStats()
            self.stats_by_model[model] = stats
        return stats

    def stats_report(self) -> str:
        """Edit telemetry grouped by model (with a total when more than one model was used)."""
        if not self.stats_by_model:
            return "edits: none this session"
        lines = [f"[{model}] {self.stats_by_model[model].summary()}"
                 for model in sorted(self.stats_by_model)]
        if len(self.stats_by_model) > 1:
            lines.append(f"[all] {self.stats.summary()}")
        return "\n".join(lines)

    def is_readonly(self, action: Action) -> bool:
        return isinstance(action, _READONLY)

    def run(self, action: Action) -> ToolResult:
        denied = self._check_permission(action)
        if denied is not None:
            return denied
        try:
            return self._dispatch(action)
        except ToolError as exc:
            return ToolResult(getattr(action, "tool", "unknown"), ok=False, output=str(exc),
                              summary="error")

    def preview(self, action: Action) -> str:
        """Unified diff of the change a write/edit would make (for the approval prompt); else ""."""
        if isinstance(action, WriteFileAction):
            return preview_write(self.project_dir, action.path, action.content)
        if isinstance(action, EditFileAction):
            return preview_edit(self.project_dir, action.path, action.edits)
        return ""

    def _check_permission(self, action: Action) -> ToolResult | None:
        """Enforce only the model-unoverridable hard denials; ASK/ALLOW proceed (see class doc)."""
        if self.gate is None:
            return None
        verdict = self.gate.check(action)
        if verdict.decision is Decision.DENY:
            return ToolResult(getattr(action, "tool", "unknown"), ok=False,
                              output=f"permission denied: {verdict.reason}", summary="denied")
        return None

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
        if isinstance(action, EditFileAction):
            prior = self._edit_failures.get(action.path, 0)
            res = apply_edit(
                self.project_dir, action.path, action.edits,
                self._stats_for(self.active_model), prior_failures=prior,
            )
            self._edit_failures[action.path] = 0 if res.ok else prior + 1
            summary = res.tier or ("ok" if res.ok else "no match")
            return ToolResult("edit_file", res.ok, res.message, summary=summary, diff=res.diff)
        if isinstance(action, WriteFileAction):
            res = write_new_file(self.project_dir, action.path, action.content)
            return ToolResult(
                "write_file", res.ok, res.message,
                summary="created" if res.ok else "refused", diff=res.diff,
            )
        return ToolResult("unknown", False, f"unknown action: {action!r}", summary="error")

    def _clamp(self, text: str) -> str:
        return truncate_output(text, max_lines=self.result_max_lines)


def _count_lines(text: str) -> str:
    n = len(text.splitlines())
    return f"{n} line(s)"


def _aggregate_stats(by_model: dict[str, EditStats]) -> EditStats:
    """Sum per-model :class:`EditStats` into one total (for the session-wide view)."""
    total = EditStats()
    for stats in by_model.values():
        total.attempts += stats.attempts
        total.exact += stats.exact
        total.whitespace += stats.whitespace
        total.fuzzy += stats.fuzzy
        total.whole_file += stats.whole_file
        total.failures += stats.failures
        total.fallbacks += stats.fallbacks
    return total
