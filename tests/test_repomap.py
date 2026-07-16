"""Tests for the repo map (TASKS 5.1): gitignore filtering, sizes, and depth/width truncation."""

from __future__ import annotations

from arescode.repo.repomap import build_repo_map


def test_lists_files_and_dirs_with_sizes(tmp_path):
    (tmp_path / "a.py").write_text("print('hi')\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "b.py").write_text("x = 1\n")

    out = build_repo_map(tmp_path)

    assert "a.py" in out
    assert "src/" in out
    assert "b.py" in out
    assert "B" in out  # a byte-size suffix is rendered


def test_respects_gitignore(tmp_path):
    (tmp_path / ".gitignore").write_text("ignored/\nsecret.txt\n")
    (tmp_path / "keep.py").write_text("1\n")
    (tmp_path / "secret.txt").write_text("nope\n")
    (tmp_path / "ignored").mkdir()
    (tmp_path / "ignored" / "x.py").write_text("1\n")

    out = build_repo_map(tmp_path)

    assert "keep.py" in out
    assert "secret.txt" not in out
    assert "ignored/" not in out


def test_skips_well_known_noise_dirs(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("x\n")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "m.pyc").write_text("x\n")
    (tmp_path / "real.py").write_text("1\n")

    out = build_repo_map(tmp_path)

    assert "real.py" in out
    assert ".git" not in out
    assert "__pycache__" not in out


def test_empty_project_returns_empty_string(tmp_path):
    assert build_repo_map(tmp_path) == ""


def test_depth_truncation_fits_budget(tmp_path):
    # A deep chain of nested dirs; a tiny token budget must collapse depth and still fit.
    node = tmp_path
    for i in range(6):
        node = node / f"level{i}"
        node.mkdir()
        (node / f"f{i}.py").write_text("x\n")

    out = build_repo_map(tmp_path, max_tokens=10)  # ~40 chars

    assert "level0/" in out       # top-level structure is always shown (breadth-first)
    assert "level3/" not in out   # deeper levels collapsed away
    assert "..." in out           # collapsed marker present


def test_width_cap_collapses_a_huge_directory(tmp_path):
    big = tmp_path / "many"
    big.mkdir()
    for i in range(150):  # more than MAX_ENTRIES_PER_DIR (100)
        (big / f"f{i:03}.py").write_text("x\n")

    out = build_repo_map(tmp_path)

    assert "more files" in out  # the width cap collapsed the tail into a summary line
