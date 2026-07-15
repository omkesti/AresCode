"""Tests for the model-picker selection logic (D12). The interactive read is thin; this covers
the pure ``resolve_choice`` mapping used by ``/model`` with no argument."""

from __future__ import annotations

from arescode.ui.model_select import resolve_choice

NAMES = ["qwen2.5-coder:7b", "qwen2.5-coder:14b-instruct"]


def test_number_selects_by_index():
    assert resolve_choice("1", NAMES) == "qwen2.5-coder:7b"
    assert resolve_choice("2", NAMES) == "qwen2.5-coder:14b-instruct"


def test_out_of_range_number_is_cancel():
    assert resolve_choice("0", NAMES) is None
    assert resolve_choice("9", NAMES) is None


def test_empty_is_cancel():
    assert resolve_choice("", NAMES) is None
    assert resolve_choice("   ", NAMES) is None


def test_name_is_passed_through_for_matching():
    # A non-numeric answer is returned verbatim; the REPL resolves it via match_model.
    assert resolve_choice("qwen2.5-coder:14b", NAMES) == "qwen2.5-coder:14b"
