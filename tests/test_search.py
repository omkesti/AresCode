"""Tests for grep and glob (TASKS 2.4)."""

from __future__ import annotations

from arescode.tools.search import _grep_python, glob_files, grep


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
