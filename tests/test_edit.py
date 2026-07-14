"""Tests for the edit cascade, whole-file fallback, write_file, and telemetry (TASKS 3.1-3.7)."""

from __future__ import annotations

from arescode.config import Config
from arescode.tools.edit import (
    Ambiguous,
    EditStats,
    Match,
    NoMatch,
    apply_edit,
    find_match,
    write_new_file,
)
from arescode.tools.registry import EditFileAction, Executor, SearchReplace, WriteFileAction


def sr(search: str, replace: str) -> SearchReplace:
    return SearchReplace(search, replace)


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# --- find_match cascade tiers ---------------------------------------------


def test_find_exact():
    file_lines = ["a", "b", "c"]
    m = find_match(file_lines, ["b"])
    assert isinstance(m, Match) and m.tier == "exact" and (m.start, m.end) == (1, 2)


def test_find_whitespace_tier():
    file_lines = ["def f():", "    return 1   "]  # trailing whitespace in the file
    m = find_match(file_lines, ["def f():", "    return 1"])
    assert isinstance(m, Match) and m.tier == "whitespace"


def test_find_fuzzy_tier():
    file_lines = ["def add(a, b):", "    return a + b"]
    m = find_match(file_lines, ["def add(a, b):", "    return a+b"])  # missing spaces
    assert isinstance(m, Match) and m.tier == "fuzzy" and m.ratio > 0.9


def test_find_ambiguous_exact():
    file_lines = ["x = 1", "y = 2", "x = 1"]
    assert isinstance(find_match(file_lines, ["x = 1"]), Ambiguous)


def test_find_no_match_below_threshold():
    file_lines = ["the quick brown fox", "jumps over"]
    outcome = find_match(file_lines, ["completely unrelated content here"])
    assert isinstance(outcome, NoMatch)
    assert outcome.best_ratio < 0.9


def test_find_fuzzy_single_region_not_ambiguous():
    # Two adjacent near-identical lines shouldn't read as two distinct regions.
    file_lines = ["value = 1", "value = 1", "other"]
    m = find_match(file_lines, ["value = 2"])
    # best fuzzy match is unique-ish region; must not raise Ambiguous incorrectly
    assert isinstance(m, (Match, NoMatch))


# --- apply_edit ------------------------------------------------------------


def test_apply_exact_edit_and_diff(tmp_path):
    write(tmp_path, "m.py", "def add(a, b):\n    return a + b\n")
    stats = EditStats()
    res = apply_edit(tmp_path, "m.py", (sr("    return a + b", "    return a - b"),), stats)
    assert res.ok and res.tier == "exact"
    assert (tmp_path / "m.py").read_text() == "def add(a, b):\n    return a - b\n"
    assert "-    return a + b" in res.diff and "+    return a - b" in res.diff
    assert stats.exact == 1 and stats.attempts == 1


def test_apply_preserves_no_trailing_newline(tmp_path):
    write(tmp_path, "m.txt", "one\ntwo")  # no trailing newline
    apply_edit(tmp_path, "m.txt", (sr("two", "TWO"),), EditStats())
    assert (tmp_path / "m.txt").read_text() == "one\nTWO"


def test_apply_multiple_edits_sequential(tmp_path):
    write(tmp_path, "m.py", "a = 1\nb = 2\nc = 3\n")
    res = apply_edit(tmp_path, "m.py", (sr("a = 1", "a = 10"), sr("c = 3", "c = 30")), EditStats())
    assert res.ok
    assert (tmp_path / "m.py").read_text() == "a = 10\nb = 2\nc = 30\n"


def test_apply_ambiguous_reports_failure(tmp_path):
    write(tmp_path, "m.py", "x = 1\ny = 2\nx = 1\n")
    stats = EditStats()
    res = apply_edit(tmp_path, "m.py", (sr("x = 1", "x = 9"),), stats)
    assert not res.ok and "2 places" in res.message
    assert stats.failures == 1


def test_apply_no_match_feedback(tmp_path):
    write(tmp_path, "m.py", "alpha\nbeta\ngamma\n")
    res = apply_edit(tmp_path, "m.py", (sr("nonexistent line here", "x"),), EditStats())
    assert not res.ok
    assert "SEARCH block not found" in res.message
    assert "Closest match" in res.message and "similar" in res.message


def test_apply_missing_file(tmp_path):
    res = apply_edit(tmp_path, "nope.py", (sr("a", "b"),), EditStats())
    assert not res.ok and "not found" in res.message


def test_edit_rejected_if_it_breaks_python_syntax(tmp_path):
    write(tmp_path, "m.py", "def f():\n    return 1\n")
    # Un-indenting the body produces invalid Python; the edit must be rejected, file untouched.
    res = apply_edit(tmp_path, "m.py", (sr("    return 1", "return 1"),), EditStats())
    assert not res.ok and "break" in res.message.lower()
    assert (tmp_path / "m.py").read_text() == "def f():\n    return 1\n"


# --- whole-file fallback ---------------------------------------------------


def test_whole_file_replace(tmp_path):
    write(tmp_path, "m.py", "old line 1\nold line 2\n")
    stats = EditStats()
    res = apply_edit(tmp_path, "m.py", (sr("", "brand new\ncontents\n"),), stats)
    assert res.ok and res.tier == "whole_file"
    assert (tmp_path / "m.py").read_text() == "brand new\ncontents\n"
    assert stats.whole_file == 1 and stats.fallbacks == 1


def test_whole_file_rejects_elided_content(tmp_path):
    write(tmp_path, "m.py", "real\ncontent\n")
    elided = sr("", "def f():\n    # ... rest unchanged ...\n")
    res = apply_edit(tmp_path, "m.py", (elided,), EditStats())
    assert not res.ok and "truncated" in res.message
    assert (tmp_path / "m.py").read_text() == "real\ncontent\n"  # unchanged


def test_no_match_escalates_to_whole_file_on_small_file(tmp_path):
    write(tmp_path, "small.py", "one\ntwo\n")  # < 150 lines
    res = apply_edit(tmp_path, "small.py", (sr("zzz", "x"),), EditStats(), prior_failures=0)
    assert "empty SEARCH block" in res.message


def test_no_match_escalates_only_after_cap_on_large_file(tmp_path):
    write(tmp_path, "big.py", "\n".join(f"line{i}" for i in range(200)) + "\n")
    first = apply_edit(tmp_path, "big.py", (sr("zzz", "x"),), EditStats(), prior_failures=0)
    later = apply_edit(tmp_path, "big.py", (sr("zzz", "x"),), EditStats(), prior_failures=2)
    assert "empty SEARCH block" not in first.message
    assert "empty SEARCH block" in later.message


# --- write_file ------------------------------------------------------------


def test_write_new_file_creates_parents(tmp_path):
    res = write_new_file(tmp_path, "pkg/sub/new.py", "print('hi')")
    assert res.ok
    assert (tmp_path / "pkg" / "sub" / "new.py").read_text() == "print('hi')\n"


def test_write_refuses_existing(tmp_path):
    write(tmp_path, "there.py", "x\n")
    res = write_new_file(tmp_path, "there.py", "y")
    assert not res.ok and "already exists" in res.message


def test_write_rejects_elided(tmp_path):
    res = write_new_file(tmp_path, "e.py", "def f():\n    # ... rest ...\n")
    assert not res.ok


# --- telemetry -------------------------------------------------------------


def test_stats_summary_counts():
    stats = EditStats()
    stats.attempts = 3
    stats.record_tier("exact")
    stats.record_tier("fuzzy")
    stats.failures = 1
    s = stats.summary()
    assert "3 attempted" in s and "exact=1" in s and "fuzzy=1" in s


# --- executor integration + retry counter ---------------------------------


def test_executor_edit_updates_stats_and_failure_counter(tmp_path):
    write(tmp_path, "m.py", "value = 1\n")
    ex = Executor(tmp_path, Config())

    good = ex.run(EditFileAction("m.py", (SearchReplace("value = 1", "value = 2"),)))
    assert good.ok and good.summary == "exact"
    assert ex.stats.exact == 1
    assert ex._edit_failures["m.py"] == 0

    bad = ex.run(EditFileAction("m.py", (SearchReplace("not here", "x"),)))
    assert not bad.ok
    assert ex._edit_failures["m.py"] == 1


def test_executor_write_file(tmp_path):
    ex = Executor(tmp_path, Config())
    res = ex.run(WriteFileAction("new.py", "print(1)"))
    assert res.ok and res.summary == "created"
    assert (tmp_path / "new.py").exists()
