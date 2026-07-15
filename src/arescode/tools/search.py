"""Search tools: grep (ripgrep wrapper + pure-Python fallback) and glob/list_dir.

Both respect .gitignore via pathspec and skip well-known noise directories
(context.md §4.4, TASKS 2.4).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pathspec

from arescode.tools.base import ToolError

DEFAULT_IGNORE = {".git", ".venv", "venv", "__pycache__", "node_modules", ".mypy_cache",
                  ".pytest_cache", ".ruff_cache"}
GREP_MAX_MATCHES = 100
GLOB_MAX_RESULTS = 500
GLOB_MAX_DEPTH = 25


def _load_gitignore(project_dir: Path) -> pathspec.PathSpec | None:
    gitignore = project_dir / ".gitignore"
    if not gitignore.is_file():
        return None
    lines = gitignore.read_text(encoding="utf-8").splitlines()
    return pathspec.PathSpec.from_lines("gitignore", lines)


def _is_ignored(rel: Path, spec: pathspec.PathSpec | None) -> bool:
    if any(part in DEFAULT_IGNORE for part in rel.parts):
        return True
    if spec is not None and spec.match_file(rel.as_posix()):
        return True
    return False


def _iter_files(project_dir: Path, spec: pathspec.PathSpec | None) -> Iterator[Path]:
    for path in project_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(project_dir)
        if _is_ignored(rel, spec):
            continue
        yield path


def grep(
    project_dir: Path,
    pattern: str,
    path: str | None = None,
    glob: str | None = None,
    *,
    max_matches: int = GREP_MAX_MATCHES,
) -> tuple[str, int]:
    """Search for ``pattern`` and return (formatted matches, count).

    Prefers the ``rg`` binary; falls back to a pure-Python scan when ripgrep is absent.
    """
    rg = shutil.which("rg")
    if rg:
        return _grep_ripgrep(rg, project_dir, pattern, path, glob, max_matches)
    return _grep_python(project_dir, pattern, path, glob, max_matches)


def _grep_ripgrep(rg, project_dir, pattern, path, glob, max_matches):
    cmd = [rg, "--line-number", "--no-heading", "--color", "never"]
    if glob:
        cmd += ["--glob", glob]
    cmd += ["--", pattern]
    if path:
        cmd.append(path)
    try:
        proc = subprocess.run(
            cmd, cwd=project_dir, capture_output=True, text=True, timeout=30
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError("grep timed out") from exc
    if proc.returncode not in (0, 1):  # 1 == no matches; anything else is an error
        raise ToolError(f"grep failed: {proc.stderr.strip() or 'ripgrep error'}")
    lines = proc.stdout.splitlines()
    return _format_matches(lines, max_matches)


def _grep_python(project_dir, pattern, path, glob, max_matches):
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        raise ToolError(f"invalid regex: {exc}") from exc
    spec = _load_gitignore(project_dir)
    root = (project_dir / path) if path else project_dir
    glob_spec = pathspec.PathSpec.from_lines("gitignore", [glob]) if glob else None

    matches: list[str] = []
    files: Iterator[Path]
    if root.is_file():
        files = iter([root])
    else:
        files = _iter_files(root if root.is_dir() else project_dir, spec)
    for file in files:
        rel = file.relative_to(project_dir)
        if glob_spec is not None and not glob_spec.match_file(rel.as_posix()):
            continue
        try:
            text = file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                matches.append(f"{rel.as_posix()}:{lineno}:{line}")
                if len(matches) > max_matches:
                    return _format_matches(matches, max_matches)
    return _format_matches(matches, max_matches)


def _format_matches(lines: list[str], max_matches: int) -> tuple[str, int]:
    count = len(lines)
    if count == 0:
        return "(no matches)", 0
    shown = lines[:max_matches]
    out = "\n".join(shown)
    if count > max_matches:
        out += f"\n... [{count - max_matches} more matches; narrow the pattern or add a path]"
    return out, count


def _normalize_glob_pattern(pattern: str) -> str:
    """Coerce a model-supplied glob into a project-root-relative POSIX pattern.

    Weaker models routinely anchor patterns (``/docs/*.md``), prefix a drive (``C:/docs``),
    use OS separators (``docs\\*.md``), or lead with ``./``. Paths are relative to the project
    root by contract (see prompts/system.md), and ``Path.glob`` raises ``NotImplementedError``
    on an anchored pattern — so we strip the anchor and let the glob run inside the root rather
    than crash. This also confines the search to the project (an absolute pattern can't escape).
    """
    pat = pattern.strip().replace("\\", "/")
    if len(pat) >= 2 and pat[1] == ":":  # drop a Windows drive like "C:/..."
        pat = pat[2:]
    pat = pat.lstrip("/")  # anchored -> relative to the project root
    while pat.startswith("./"):
        pat = pat[2:]
    return pat or "*"


def glob_files(
    project_dir: Path,
    pattern: str,
    *,
    max_results: int = GLOB_MAX_RESULTS,
    max_depth: int = GLOB_MAX_DEPTH,
) -> tuple[str, int]:
    """List files matching a glob ``pattern`` (e.g. ``**/*.py``), gitignore-filtered."""
    spec = _load_gitignore(project_dir)
    normalized = _normalize_glob_pattern(pattern)
    results: list[str] = []
    try:
        matched = list(project_dir.glob(normalized))
    except (NotImplementedError, ValueError) as exc:
        # A pattern pathlib still refuses (e.g. empty or otherwise malformed): report it back to
        # the model as a tool error so it can retry, rather than crashing the agent loop.
        raise ToolError(f"invalid glob pattern {pattern!r}: {exc}") from exc
    for path in matched:
        if not path.is_file():
            continue
        rel = path.relative_to(project_dir)
        if len(rel.parts) > max_depth or _is_ignored(rel, spec):
            continue
        results.append(rel.as_posix())
    results.sort()
    count = len(results)
    if count == 0:
        return "(no files matched)", 0
    shown = results[:max_results]
    out = "\n".join(shown)
    if count > max_results:
        out += f"\n... [{count - max_results} more files]"
    return out, count
