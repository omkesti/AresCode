"""Tests for read_file (TASKS 2.4)."""

from __future__ import annotations

import pytest

from arescode.tools.base import ToolError
from arescode.tools.files import read_file


def test_read_numbers_lines(tmp_path):
    (tmp_path / "a.txt").write_text("first\nsecond\nthird\n")
    out = read_file(tmp_path, "a.txt")
    assert "     1\tfirst" in out
    assert "     3\tthird" in out


def test_offset_and_limit(tmp_path):
    (tmp_path / "a.txt").write_text("\n".join(f"line{i}" for i in range(1, 11)))
    out = read_file(tmp_path, "a.txt", offset=3, limit=2)
    assert "     3\tline3" in out
    assert "     4\tline4" in out
    assert "line5" not in out


def test_missing_file_raises(tmp_path):
    with pytest.raises(ToolError, match="not found"):
        read_file(tmp_path, "nope.txt")


def test_directory_raises(tmp_path):
    (tmp_path / "sub").mkdir()
    with pytest.raises(ToolError, match="directory"):
        read_file(tmp_path, "sub")


def test_offset_past_end_raises(tmp_path):
    (tmp_path / "a.txt").write_text("only one line")
    with pytest.raises(ToolError, match="past the end"):
        read_file(tmp_path, "a.txt", offset=99)


def test_empty_file(tmp_path):
    (tmp_path / "empty.txt").write_text("")
    assert read_file(tmp_path, "empty.txt") == "(empty file)"


def test_max_lines_cap_adds_footer(tmp_path):
    (tmp_path / "big.txt").write_text("\n".join(str(i) for i in range(1, 51)))
    out = read_file(tmp_path, "big.txt", max_lines=10)
    assert "more lines" in out
    assert "offset=11" in out
