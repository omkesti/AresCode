"""Repo map: a gitignore-filtered file tree with sizes, capped ~1,500 tokens.

Built once at session start and injected into the system prompt so the model has a bird's-eye
view of the project without spending tool calls to discover its shape (context.md §4.5, TASKS 5.1).
The `/map` command re-displays it.

Two caps keep a large repo from blowing the token budget:

- **depth** — the tree is rendered at the deepest nesting level whose text still fits the budget;
  anything deeper collapses to a ``...(N files, M dirs)`` summary. This is the "breadth-first
  truncation" of TASKS 5.1: the top-level structure is always shown, detail is shed from the bottom.
- **width** — a single pathological directory is capped at :data:`MAX_ENTRIES_PER_DIR` children,
  with a ``...(+K more)`` line, so one giant folder can't dominate the map.

gitignore filtering (and the well-known-noise-dir skip list) is shared with the search tools, so
the map shows exactly the files ``glob``/``grep`` would. tree-sitter top-level symbols per file are
a post-MVP addition.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import pathspec

from arescode.tools.search import _is_ignored, _load_gitignore

__all__ = ["build_repo_map", "DEFAULT_MAX_TOKENS", "MAX_ENTRIES_PER_DIR"]

DEFAULT_MAX_TOKENS = 1500
# Per-directory child cap (width). Guards against one directory with thousands of entries.
MAX_ENTRIES_PER_DIR = 100
# Safety bound on recursion into pathologically deep trees (display depth is capped separately).
MAX_SCAN_DEPTH = 30


@dataclass(slots=True)
class _Dir:
    """A directory node: child directories (name -> node) and files (name, size)."""

    dirs: dict[str, _Dir] = field(default_factory=dict)
    files: list[tuple[str, int]] = field(default_factory=list)


def build_repo_map(project_dir: Path | str, *, max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
    """Return the repo map as indented text, or ``""`` for an empty/unreadable project."""
    root = Path(project_dir)
    tree = _scan(root, Path("."), _load_gitignore(root), depth=0)
    if not tree.dirs and not tree.files:
        return ""

    budget_chars = max(1, max_tokens) * 4  # len // 4 token estimate, inverted
    lines: list[str] = []
    # Render at decreasing depth until the text fits the budget: full detail first, then shed the
    # deepest levels (breadth-first truncation). depth_cap == 0 always terminates the loop.
    for depth_cap in range(_max_depth(tree), -1, -1):
        lines = _render(tree, depth_cap)
        if _text_length(lines) <= budget_chars:
            break
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------


def _scan(abs_dir: Path, rel_dir: Path, spec: pathspec.PathSpec | None, *, depth: int) -> _Dir:
    """Recursively build the tree under ``abs_dir``, skipping gitignored / noise paths."""
    node = _Dir()
    if depth >= MAX_SCAN_DEPTH:
        return node
    try:
        entries = sorted(os.scandir(abs_dir), key=lambda e: e.name)
    except OSError:
        return node
    for entry in entries:
        rel = rel_dir / entry.name
        try:
            is_dir = entry.is_dir(follow_symlinks=False)
        except OSError:
            continue
        if _entry_ignored(rel, is_dir, spec):
            continue
        if is_dir:
            node.dirs[entry.name] = _scan(Path(entry.path), rel, spec, depth=depth + 1)
        elif entry.is_file(follow_symlinks=False):
            node.files.append((entry.name, _safe_size(entry)))
    return node


def _entry_ignored(rel: Path, is_dir: bool, spec: pathspec.PathSpec | None) -> bool:
    """Directory-aware gitignore check.

    Reuses the shared file check, then adds the directory case: a gitignore *directory* pattern
    like ``build/`` only matches when the queried path carries a trailing slash, so a bare
    directory name would otherwise slip through.
    """
    if _is_ignored(rel, spec):
        return True
    return bool(is_dir and spec is not None and spec.match_file(rel.as_posix() + "/"))


def _safe_size(entry: os.DirEntry) -> int:
    try:
        return entry.stat(follow_symlinks=False).st_size
    except OSError:
        return 0


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _render(tree: _Dir, depth_cap: int) -> list[str]:
    lines: list[str] = []
    _emit(tree, prefix="", depth=0, depth_cap=depth_cap, lines=lines)
    return lines


def _emit(node: _Dir, *, prefix: str, depth: int, depth_cap: int, lines: list[str]) -> None:
    dir_items = sorted(node.dirs.items())
    shown_dirs = dir_items[:MAX_ENTRIES_PER_DIR]
    for name, child in shown_dirs:
        lines.append(f"{prefix}{name}/")
        if depth < depth_cap:
            _emit(child, prefix=prefix + "  ", depth=depth + 1, depth_cap=depth_cap, lines=lines)
        else:
            nfiles, ndirs = _subtree_counts(child)
            if nfiles or ndirs:
                summary = _plural(nfiles, "file")
                if ndirs:
                    summary += f", {_plural(ndirs, 'dir')}"
                lines.append(f"{prefix}  ...({summary})")
    if len(dir_items) > len(shown_dirs):
        lines.append(f"{prefix}...(+{len(dir_items) - len(shown_dirs)} more dirs)")

    shown_files = node.files[:MAX_ENTRIES_PER_DIR]
    for name, size in shown_files:
        lines.append(f"{prefix}{name}  {_fmt_size(size)}")
    if len(node.files) > len(shown_files):
        lines.append(f"{prefix}...(+{len(node.files) - len(shown_files)} more files)")


def _subtree_counts(node: _Dir) -> tuple[int, int]:
    """Total (files, dirs) in the subtree rooted at ``node`` (for the collapsed summary line)."""
    nfiles = len(node.files)
    ndirs = len(node.dirs)
    for child in node.dirs.values():
        cf, cd = _subtree_counts(child)
        nfiles += cf
        ndirs += cd
    return nfiles, ndirs


def _max_depth(node: _Dir) -> int:
    if not node.dirs:
        return 0
    return 1 + max(_max_depth(child) for child in node.dirs.values())


def _text_length(lines: list[str]) -> int:
    return sum(len(line) + 1 for line in lines)  # +1 for the joining newline


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f}K"
    return f"{n / (1024 * 1024):.1f}M"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")
