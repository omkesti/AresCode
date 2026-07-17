"""Lenient parser: extract tool actions and SEARCH/REPLACE edit blocks from raw completions.

Written on the assumption the model *almost* gets the format right, so it absorbs marker-length
drift (``<<<<<<`` vs ``<<<<<<<``), stray whitespace, missing closing tags, filenames placed a
few lines off, nested fences, and multiple actions per response, in any order
(context.md §4.2-4.3, TASKS 2.1).

Authored under an explicit user override of decision D10 for Phase 2. The write_file body
extraction was later hardened (line-based fence scan, ``_fenced_blocks`` / ``_write_content``) by
Claude Code under the author's explicit request, to absorb a real recurring failure where the model
wraps the tool tag in its own fence and the code lands in a *second* fence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from arescode.tools.registry import (
    Action,
    BashAction,
    EditFileAction,
    GlobAction,
    GrepAction,
    ReadFileAction,
    SearchReplace,
    WriteFileAction,
)

# A tool invocation opener: <tool>name</tool>, case/space tolerant.
TOOL_RE = re.compile(r"<tool>\s*([a-zA-Z_]+)\s*</tool>", re.IGNORECASE)

# A SEARCH/REPLACE block. Marker length >= 3 (drift-tolerant); spacing on marker lines optional.
SR_RE = re.compile(
    r"^<{3,}[ \t]*SEARCH[ \t]*$\n(.*?)^={3,}[ \t]*$\n(.*?)^>{3,}[ \t]*REPLACE[ \t]*$",
    re.DOTALL | re.MULTILINE | re.IGNORECASE,
)

# Bare-marker fallback: the labels SEARCH / REPLACE with NO git-conflict markers (tolerating
# markdown bold or a trailing colon). Only ever applied inside a <tool>edit_file</tool> window (see
# _parse_tool), so prose that merely mentions SEARCH/REPLACE is never mistaken for an edit. Absorbs
# a real qwen2.5-coder:7b failure from the 6.5 dogfood (LATER.md): the model wrote the labels
# without markers, so SR_RE missed the block and the edit was silently dropped.
_BARE_SR_RE = re.compile(
    r"^[ \t]*\*{0,2}[ \t]*SEARCH[ \t]*\*{0,2}[ \t]*:?[ \t]*$\n(.*?)"
    r"^[ \t]*\*{0,2}[ \t]*REPLACE[ \t]*\*{0,2}[ \t]*:?[ \t]*$\n(.*)",
    re.DOTALL | re.MULTILINE | re.IGNORECASE,
)

_PATHY = re.compile(r"^[\w./\\-]+$")
_PATH_TAG = re.compile(r"<path>\s*(.*?)\s*</path>", re.IGNORECASE)


@dataclass(slots=True)
class ParseResult:
    actions: list[Action]
    prose: str  # the completion with recognized action syntax stripped (for display)


def parse(text: str) -> ParseResult:
    """Parse a raw model completion into ordered actions plus leftover prose."""
    found: list[tuple[int, Action]] = []

    # SEARCH/REPLACE edit blocks. A filename usually sits just above; consecutive blocks may
    # share the filename of the previous block.
    last_filename: str | None = None
    for m in SR_RE.finditer(text):
        search = m.group(1).removesuffix("\n")
        replace = m.group(2).removesuffix("\n")
        filename = _find_filename(text, m.start()) or last_filename
        if not filename:
            continue  # can't apply an edit without a target; drop it
        last_filename = filename
        edit = EditFileAction(path=filename, edits=(SearchReplace(search, replace),))
        found.append((m.start(), edit))

    # Tagged tool invocations.
    for m in TOOL_RE.finditer(text):
        action = _parse_tool(text, m)
        if action is not None:
            found.append((m.start(), action))

    found.sort(key=lambda item: item[0])
    return ParseResult(actions=[a for _, a in found], prose=_strip_actions(text))


def parse_actions(text: str) -> list[Action]:
    """Convenience wrapper returning just the ordered actions."""
    return parse(text).actions


# ---------------------------------------------------------------------------
# Tool-tag parsing
# ---------------------------------------------------------------------------


def _parse_tool(text: str, match: re.Match[str]) -> Action | None:
    name = match.group(1).lower()
    window = text[match.end() : _window_end(text, match.end())]

    if name == "read_file":
        path = _param(window, "path")
        if not path:
            return None
        return ReadFileAction(path, _int(_param(window, "offset")), _int(_param(window, "limit")))
    if name == "grep":
        pattern = _param(window, "pattern")
        if not pattern:
            return None
        return GrepAction(pattern, _param(window, "path"), _param(window, "glob"))
    if name in ("glob", "list_dir"):
        pattern = _param(window, "pattern") or _param(window, "path")
        return GlobAction(pattern) if pattern else None
    if name == "bash":
        cmd = _param(window, "cmd") or _param(window, "command")
        return BashAction(cmd) if cmd else None
    if name == "write_file":
        path = _param(window, "path")
        content = _write_content(window)
        return WriteFileAction(path, content or "") if path else None
    if name == "edit_file":
        # A proper <<<<<<< SEARCH block is handled by SR_RE in parse(); this branch only rescues
        # the bare-marker variant (no conflict markers), scoped to this tool's window so it can't
        # misread prose. See _BARE_SR_RE / _bare_edit_block and the 6.5 dogfood note in LATER.md.
        path = _param(window, "path")
        edit = _bare_edit_block(window)
        return EditFileAction(path, (edit,)) if (path and edit is not None) else None
    return None  # unknown tool name


def _window_end(text: str, start: int) -> int:
    """End of a tool's parameter window: the next tool tag or edit block, or end of text."""
    end = len(text)
    nxt = TOOL_RE.search(text, start)
    if nxt:
        end = min(end, nxt.start())
    sr = SR_RE.search(text, start)
    if sr:
        end = min(end, sr.start())
    return end


def _param(window: str, name: str) -> str | None:
    """Extract <name>...</name>, tolerating a missing closing tag (take to end of line)."""
    closed = re.search(rf"<{name}>(.*?)</{name}>", window, re.DOTALL | re.IGNORECASE)
    if closed:
        return closed.group(1).strip()
    opened = re.search(rf"<{name}>[ \t]*(.+)", window, re.IGNORECASE)
    if opened:
        return opened.group(1).splitlines()[0].strip()
    return None


def _fenced_blocks(window: str) -> list[str]:
    """Every fenced code block in ``window``, in order, as a line-based scan (not one regex).

    A single lazy ```...``` regex pairs the first ``` with the *next* ```, which breaks on the
    model's common malformed write_file shape — it wraps the tag in its own fence, so the code
    lands in a *second* fence:

        ```
        ```python
        <code>
        ```

    Regex-pairing captures the empty block between the first two ```; the code is lost. Scanning by
    line fixes this: any line whose first non-space token starts with ``` ends the current block
    (whether it is a bare closing fence or the opening of the next one), so the empty fence and the
    code fence come out as separate blocks and the caller can pick the non-empty one.
    """
    lines = window.split("\n")
    blocks: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        if lines[i].lstrip().startswith("```"):
            i += 1  # consume the opening fence line
            body: list[str] = []
            while i < n and not lines[i].lstrip().startswith("```"):
                body.append(lines[i])
                i += 1
            if i < n and lines[i].strip() == "```":  # consume a bare closing fence (not a new open)
                i += 1
            blocks.append("\n".join(body))
        else:
            i += 1
    return blocks


def _write_content(window: str) -> str | None:
    """The body for a write_file: the first non-empty fenced block, else a <content> tag."""
    for block in _fenced_blocks(window):
        if block.strip():
            return block
    return _param(window, "content")


def _strip_blank_edges(text: str) -> str:
    """Drop leading/trailing blank lines while preserving the first content line's indentation.

    (A plain ``.strip()`` would eat the indentation of an indented method body and break the match.)
    """
    lines = text.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def _unfence(chunk: str) -> str:
    """One side of a bare edit: the first non-empty fenced block if it's fenced, else the raw text.

    The bare-marker variant sometimes wraps each side in a ```lang ... ``` fence and sometimes
    leaves it plain; either way we want just the code, blank edges trimmed.
    """
    for block in _fenced_blocks(chunk):
        if block.strip():
            return _strip_blank_edges(block)
    return _strip_blank_edges(chunk)


def _bare_edit_block(window: str) -> SearchReplace | None:
    """Recover a SEARCH/REPLACE pair written with bare labels (no markers) — see _BARE_SR_RE.

    Returns ``None`` when there is no bare block or the SEARCH side is empty: an empty bare SEARCH
    is too ambiguous to trust here (a genuine whole-file/new-file edit uses markers or write_file),
    so we decline rather than risk a spurious match.
    """
    m = _BARE_SR_RE.search(window)
    if m is None:
        return None
    search = _unfence(m.group(1))
    if not search:
        return None
    return SearchReplace(search, _unfence(m.group(2)))


def _int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value.strip())
    except (ValueError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Filename hunting for SEARCH/REPLACE blocks
# ---------------------------------------------------------------------------


def _find_filename(text: str, block_start: int, lookback: int = 4) -> str | None:
    """Hunt up to ~3 lines above a block for the file it targets."""
    lines = text[:block_start].splitlines()
    for raw in reversed(lines[-lookback:]):
        tag = _PATH_TAG.search(raw)
        if tag and _looks_like_path(tag.group(1)):
            return tag.group(1).strip()
        cand = re.sub(r"^(file|path)\s*:\s*", "", raw.strip(), flags=re.IGNORECASE)
        cand = cand.strip().strip("`").strip()
        if _looks_like_path(cand):
            return cand
    return None


def _looks_like_path(s: str) -> bool:
    s = s.strip().strip("`").strip()
    if not s or " " in s or not _PATHY.match(s):
        return False
    return "/" in s or "\\" in s or "." in s


# ---------------------------------------------------------------------------
# Prose extraction (for the UI; not correctness-critical)
# ---------------------------------------------------------------------------


def _strip_actions(text: str) -> str:
    t = SR_RE.sub("", text)
    t = re.sub(r"<tool>.*?</tool>", "", t, flags=re.DOTALL | re.IGNORECASE)
    for tag in ("path", "cmd", "command", "pattern", "glob", "offset", "limit", "content"):
        t = re.sub(rf"<{tag}>.*?</{tag}>", "", t, flags=re.DOTALL | re.IGNORECASE)
    return t.strip()
