"""File tools: read_file (numbered lines, offset/limit, size caps).

write_file lands in Phase 3; only the read path is implemented here (context.md §4.4, TASKS 2.4).
"""

from __future__ import annotations

from pathlib import Path

from arescode.tools.base import ToolError

MAX_LINES = 2000
MAX_BYTES = 50_000
_IGNORE_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules"}


def _suggest_paths(project_dir: Path, path: str, limit: int = 3) -> list[str]:
    """Find files sharing the basename of a not-found path (helps the model self-correct)."""
    name = Path(path).name
    if not name:
        return []
    hits: list[str] = []
    for p in project_dir.rglob(name):
        rel = p.relative_to(project_dir)
        if p.is_file() and not any(part in _IGNORE_DIRS for part in rel.parts):
            hits.append(rel.as_posix())
            if len(hits) >= limit:
                break
    return hits


def read_file(
    project_dir: Path,
    path: str,
    offset: int | None = None,
    limit: int | None = None,
    *,
    max_lines: int = MAX_LINES,
    max_bytes: int = MAX_BYTES,
) -> str:
    """Return ``path`` as numbered lines (``cat -n`` style), capped for safety.

    ``offset`` is a 1-based starting line; ``limit`` caps the number of lines returned.
    """
    target = (project_dir / path).resolve()
    if not target.exists():
        suggestions = _suggest_paths(project_dir, path)
        message = f"file not found: {path}"
        if suggestions:
            message += f". Did you mean: {', '.join(suggestions)}? (use the exact path)"
        raise ToolError(message)
    if target.is_dir():
        raise ToolError(f"{path} is a directory, not a file")

    data = target.read_bytes()
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    total = len(lines)

    start = (offset - 1) if offset and offset > 0 else 0
    if total and start >= total:
        raise ToolError(f"offset {offset} is past the end of {path} ({total} lines)")

    span = limit if limit is not None else max_lines
    end = min(total, start + span)
    selected = lines[start:end]

    # Secondary byte cap so a file of very long lines can't blow up the context window.
    rendered: list[str] = []
    used = 0
    truncated_bytes = False
    for i, line in enumerate(selected, start=start + 1):
        entry = f"{i:6d}\t{line}"
        used += len(entry) + 1
        if used > max_bytes:
            truncated_bytes = True
            break
        rendered.append(entry)

    body = "\n".join(rendered)
    shown_end = start + len(rendered)
    if truncated_bytes:
        body += f"\n... [stopped at {max_bytes} bytes; use offset/limit to read further]"
    elif shown_end < total:
        body += f"\n... [{total - shown_end} more lines; use offset={shown_end + 1} to continue]"
    return body or "(empty file)"
