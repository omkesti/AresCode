"""SEARCH/REPLACE edit applier: the exact -> whitespace-normalized -> fuzzy matching cascade.

Finds a SEARCH block in a file (tolerating whitespace and small drift), rejects ambiguous
matches, and on a miss returns actionable feedback (closest snippet, line, similarity). An
empty SEARCH block means "replace the whole file" — the whole-file fallback the harness escalates
to after repeated misses; that path validates the replacement looks complete before writing.
Also holds write_file (new files only) and per-session edit telemetry
(context.md §4.3, TASKS 3.1-3.4 / 3.6).

Authored under an explicit user override of decision D10 for Phase 3. The Phase 4 read-only
dry-run previews (``preview_edit`` / ``preview_write``) were added by Claude Code under the author's
explicit authorization to modify ``[HAND]`` files; the ``apply_edit`` cascade itself is unchanged.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path

from arescode.tools.base import ToolError

FUZZY_THRESHOLD = 0.9
WHOLE_FILE_LINE_LIMIT = 150  # files at/under this size fall back to whole-file rewrite eagerly
RETRY_CAP = 2  # failed edits to a path before we escalate to whole-file mode

# Markers that betray a truncated "whole file" (the model elided the boring middle).
_ELISION = re.compile(
    r"(\.\.\.\s*(rest|remaining|unchanged|snip|elided|previous|existing))"
    r"|(#\s*\.\.\.\s*(rest|unchanged|existing|omitted))"
    r"|(//\s*\.\.\.\s*(rest|unchanged|existing|omitted))"
    r"|(<!--\s*\.\.\.)"
    r"|(\[\s*\.\.\.\s*lines?\b)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Telemetry (TASKS 3.6)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class EditStats:
    attempts: int = 0
    exact: int = 0
    whitespace: int = 0
    fuzzy: int = 0
    whole_file: int = 0
    failures: int = 0
    fallbacks: int = 0  # whole-file rewrites actually applied

    def record_tier(self, tier: str) -> None:
        if tier == "exact":
            self.exact += 1
        elif tier == "whitespace":
            self.whitespace += 1
        elif tier == "fuzzy":
            self.fuzzy += 1
        elif tier == "whole_file":
            self.whole_file += 1
            self.fallbacks += 1

    def summary(self) -> str:
        applied = self.attempts - self.failures
        return (
            f"edits: {self.attempts} attempted, {applied} applied, {self.failures} failed | "
            f"tiers: exact={self.exact} whitespace={self.whitespace} fuzzy={self.fuzzy} "
            f"whole_file={self.whole_file}"
        )


# ---------------------------------------------------------------------------
# Match results
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Match:
    tier: str  # "exact" | "whitespace" | "fuzzy"
    start: int  # 0-based line index (inclusive)
    end: int  # 0-based line index (exclusive)
    ratio: float = 1.0


@dataclass(slots=True)
class Ambiguous:
    tier: str
    count: int


@dataclass(slots=True)
class NoMatch:
    best_start: int | None
    best_ratio: float
    snippet: str


MatchOutcome = Match | Ambiguous | NoMatch


def find_match(file_lines: list[str], search_lines: list[str]) -> MatchOutcome:
    """Locate ``search_lines`` within ``file_lines`` via the cascade."""
    n = len(search_lines)
    if n == 0 or n > len(file_lines):
        return NoMatch(None, 0.0, "")

    windows = range(len(file_lines) - n + 1)

    # Tier 1: exact line-for-line match.
    exact = [i for i in windows if file_lines[i : i + n] == search_lines]
    if len(exact) == 1:
        return Match("exact", exact[0], exact[0] + n, 1.0)
    if len(exact) >= 2:
        return Ambiguous("exact", len(exact))

    # Tier 2: ignore trailing whitespace on every line.
    search_rs = [s.rstrip() for s in search_lines]
    ws = [i for i in windows if [f.rstrip() for f in file_lines[i : i + n]] == search_rs]
    if len(ws) == 1:
        return Match("whitespace", ws[0], ws[0] + n, 1.0)
    if len(ws) >= 2:
        return Ambiguous("whitespace", len(ws))

    # Tier 3: fuzzy match on the joined block text.
    search_text = "\n".join(search_lines)
    matcher = difflib.SequenceMatcher(autojunk=False)
    matcher.set_seq2(search_text)
    best_ratio, best_start = 0.0, 0
    high: list[int] = []
    for i in windows:
        matcher.set_seq1("\n".join(file_lines[i : i + n]))
        ratio = matcher.ratio()
        if ratio > best_ratio:
            best_ratio, best_start = ratio, i
        if ratio > FUZZY_THRESHOLD:
            high.append(i)

    if high:
        # Adjacent windows around one location aren't ambiguous; distinct regions are.
        clusters = 1 + sum(1 for a, b in zip(high, high[1:], strict=False) if b - a > n)
        if clusters >= 2:
            return Ambiguous("fuzzy", clusters)
        return Match("fuzzy", best_start, best_start + n, best_ratio)

    snippet = "\n".join(file_lines[best_start : best_start + n])
    return NoMatch(best_start, best_ratio, snippet)


# ---------------------------------------------------------------------------
# Applying edits
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class EditResult:
    ok: bool
    message: str  # concise text fed back to the model
    diff: str = ""  # unified diff for the UI (empty on failure / no-op)
    tier: str = ""


def _validate_whole_file(text: str) -> tuple[bool, str]:
    if not text.strip():
        return False, "the replacement is empty"
    marker = _ELISION.search(text)
    if marker:
        found = marker.group(0)
        return False, f"the replacement looks truncated (found {found!r}); send the full file"
    return True, ""


def _unified_diff(path: str, before: str, after: str) -> str:
    diff = difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        lineterm="",
    )
    return "\n".join(diff)


def _reattach_trailing_newline(before: str, after: str) -> str:
    if before.endswith("\n") and not after.endswith("\n"):
        return after + "\n"
    return after


def _python_syntax_error(path: str, before: str, after: str) -> str | None:
    """If an edit turns a valid .py file invalid, return the error (so we can reject it)."""
    if not path.endswith(".py"):
        return None
    try:
        compile(before, path, "exec")
    except SyntaxError:
        return None  # the file was already broken; don't block a fix
    try:
        compile(after, path, "exec")
    except SyntaxError as exc:
        return f"line {exc.lineno}: {exc.msg}"
    return None


def apply_edit(
    project_dir: Path,
    path: str,
    edits: tuple,
    stats: EditStats,
    *,
    prior_failures: int = 0,
) -> EditResult:
    """Apply SEARCH/REPLACE ``edits`` to ``path``; returns success + diff or failure feedback."""
    target = (project_dir / path).resolve()
    if not target.exists():
        return EditResult(False, f"file not found: {path}. Use write_file to create a new file.")
    if target.is_dir():
        return EditResult(False, f"{path} is a directory")

    before = target.read_text(encoding="utf-8", errors="replace")
    lines = before.splitlines()
    small_file = len(lines) < WHOLE_FILE_LINE_LIMIT
    tiers: list[str] = []

    for sr in edits:
        stats.attempts += 1

        if sr.search.strip() == "":  # whole-file rewrite (fallback path)
            valid, why = _validate_whole_file(sr.replace)
            if not valid:
                stats.failures += 1
                return EditResult(False, f"whole-file rewrite rejected: {why}.")
            lines = sr.replace.splitlines()
            stats.record_tier("whole_file")
            tiers.append("whole_file")
            continue

        outcome = find_match(lines, sr.search.splitlines())
        if isinstance(outcome, Match):
            lines = lines[: outcome.start] + sr.replace.splitlines() + lines[outcome.end :]
            stats.record_tier(outcome.tier)
            tiers.append(outcome.tier)
        elif isinstance(outcome, Ambiguous):
            stats.failures += 1
            return EditResult(
                False,
                f"SEARCH block matches {outcome.count} places in {path} ({outcome.tier}); "
                "add more surrounding context so it's unique.",
            )
        else:  # NoMatch
            stats.failures += 1
            return EditResult(False, _no_match_feedback(path, outcome, prior_failures, small_file))

    after = _reattach_trailing_newline(before, "\n".join(lines))
    if after == before:
        tier = tiers[-1] if tiers else ""
        return EditResult(True, f"no change needed in {path} (already matches)", tier=tier)

    syntax_error = _python_syntax_error(path, before, after)
    if syntax_error:
        stats.failures += 1
        return EditResult(
            False,
            f"that edit would break {path} ({syntax_error}); the file was left unchanged. "
            "Re-read the file and retry with the exact current content.",
        )

    diff = _unified_diff(path, before, after)
    target.write_text(after, encoding="utf-8")
    return EditResult(True, f"edited {path} via {', '.join(tiers)}", diff=diff, tier=tiers[-1])


def preview_edit(project_dir: Path, path: str, edits: tuple) -> str:
    """Dry-run the edit cascade (no writes, no telemetry) to build the diff shown before applying.

    Mirrors ``apply_edit``'s matching through the same ``find_match`` cascade — so preview and apply
    agree — but never touches the file or ``EditStats``. Returns ``""`` when a clean preview can't
    be produced (a miss, an ambiguity, an invalid whole-file rewrite, or a no-op), in which case the
    caller falls back to showing the raw proposed change. ``apply_edit`` remains the single source
    of truth for what actually gets written.
    """
    target = (project_dir / path).resolve()
    if not target.exists() or target.is_dir():
        return ""
    before = target.read_text(encoding="utf-8", errors="replace")
    lines = before.splitlines()
    for sr in edits:
        if sr.search.strip() == "":  # whole-file rewrite
            valid, _ = _validate_whole_file(sr.replace)
            if not valid:
                return ""
            lines = sr.replace.splitlines()
            continue
        outcome = find_match(lines, sr.search.splitlines())
        if not isinstance(outcome, Match):
            return ""
        lines = lines[: outcome.start] + sr.replace.splitlines() + lines[outcome.end :]
    after = _reattach_trailing_newline(before, "\n".join(lines))
    if after == before:
        return ""
    return _unified_diff(path, before, after)


def preview_write(project_dir: Path, path: str, content: str) -> str:
    """The diff a ``write_file`` would produce (new file), or ``""`` if content is rejected."""
    valid, _ = _validate_whole_file(content)
    if not valid:
        return ""
    body = content if content.endswith("\n") else content + "\n"
    return _unified_diff(path, "", body)


def _no_match_feedback(path: str, outcome: NoMatch, prior_failures: int, small_file: bool) -> str:
    lines = [f"SEARCH block not found in {path}."]
    if outcome.best_start is not None and outcome.snippet:
        pct = int(round(outcome.best_ratio * 100))
        lines.append(
            f"Closest match (line {outcome.best_start + 1}, {pct}% similar):\n{outcome.snippet}"
        )
    lines.append("Re-read the file and retry with the exact current content.")
    if small_file or prior_failures >= RETRY_CAP:
        lines.append(
            "If it keeps failing, return the ENTIRE updated file using an empty SEARCH block:\n"
            "<<<<<<< SEARCH\n=======\n<full file contents>\n>>>>>>> REPLACE"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# write_file (TASKS 3.4)
# ---------------------------------------------------------------------------


def write_new_file(project_dir: Path, path: str, content: str) -> EditResult:
    """Create a new file (refuses to overwrite; creates parent directories)."""
    target = (project_dir / path).resolve()
    project_root = project_dir.resolve()
    if project_root not in target.parents and target != project_root:
        raise ToolError(f"refusing to write outside the project: {path}")
    if target.exists():
        return EditResult(False, f"{path} already exists — use edit_file to modify it")

    valid, why = _validate_whole_file(content)
    if not valid:
        return EditResult(False, f"write_file rejected: {why}.")

    body = content if content.endswith("\n") else content + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return EditResult(True, f"created {path}", diff=_unified_diff(path, "", body))
