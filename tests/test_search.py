"""Tests for grep and glob (TASKS 2.4)."""

from __future__ import annotations

import pytest

from arescode.tools.base import ToolError
from arescode.tools.search import _grep_python, _normalize_glob_pattern, glob_files, grep


def _make_project(tmp_path):
    (tmp_path / "auth.py").write_text("def login():\n    return True\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "util.py").write_text("x = 1\n")
    (tmp_path / ".gitignore").write_text("ignored.py\nbuild/\n")
    (tmp_path / "ignored.py").write_text("def login(): pass\n")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "gen.py").write_text("y = 2\n")
    return tmp_path


def test_grep_finds_match(tmp_path):
    _make_project(tmp_path)
    out, count = grep(tmp_path, "def login")
    assert count >= 1
    assert "auth.py" in out


def test_grep_no_match(tmp_path):
    _make_project(tmp_path)
    out, count = grep(tmp_path, "zzz_no_such_symbol")
    assert count == 0
    assert out == "(no matches)"


def test_grep_python_fallback_respects_gitignore(tmp_path):
    _make_project(tmp_path)
    out, count = _grep_python(tmp_path, "def login", None, None, 100)
    assert "auth.py" in out
    assert "ignored.py" not in out  # gitignored file skipped by the pure-Python scanner


def test_glob_filters_gitignore_and_noise(tmp_path):
    _make_project(tmp_path)
    out, count = glob_files(tmp_path, "**/*.py")
    files = set(out.splitlines())
    assert "auth.py" in files
    assert "sub/util.py" in files
    assert "ignored.py" not in files
    assert "build/gen.py" not in files


def test_glob_no_match(tmp_path):
    _make_project(tmp_path)
    out, count = glob_files(tmp_path, "**/*.rs")
    assert count == 0


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("/docs/*.md", "docs/*.md"),      # leading slash (the reported crash)
        ("//docs/*.md", "docs/*.md"),     # doubled anchor
        ("./sub/*.py", "sub/*.py"),       # ./ prefix
        ("C:/docs/*.md", "docs/*.md"),    # Windows drive
        ("docs\\*.md", "docs/*.md"),      # backslash separators
        ("**/*.py", "**/*.py"),           # already relative -> untouched
        ("   sub/*.py  ", "sub/*.py"),    # surrounding whitespace
        ("/", "*"),                       # bare anchor -> safe fallback
    ],
)
def test_normalize_glob_pattern(raw, expected):
    assert _normalize_glob_pattern(raw) == expected


def test_glob_anchored_pattern_does_not_crash(tmp_path):
    """Regression: an anchored pattern like '/**/*.py' must not raise NotImplementedError."""
    _make_project(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "context.md").write_text("# doc\n")

    # The exact shape a weak model emitted ("glob /docs/*.md") now resolves relative to the root.
    out, count = glob_files(tmp_path, "/docs/*.md")
    assert count == 1
    assert out.splitlines() == ["docs/context.md"]

    # A recursive anchored pattern is handled the same as the equivalent relative one.
    anchored, n_anchored = glob_files(tmp_path, "/**/*.py")
    relative, n_relative = glob_files(tmp_path, "**/*.py")
    assert (anchored, n_anchored) == (relative, n_relative)


def test_glob_blank_pattern_degrades_to_star(tmp_path):
    """A blank/whitespace pattern degrades to '*' (lists the root) instead of crashing."""
    _make_project(tmp_path)
    out, count = glob_files(tmp_path, "   ")
    assert count >= 1
    assert "auth.py" in out.splitlines()  # top-level files listed, no exception


def test_glob_degenerate_pattern_is_reported_not_crash(tmp_path):
    """A pattern pathlib still refuses ('.') surfaces as a retryable ToolError, never a crash."""
    _make_project(tmp_path)
    with pytest.raises(ToolError):
        glob_files(tmp_path, ".")
