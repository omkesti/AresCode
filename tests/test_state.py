"""Tests for the session state / history model (TASKS 1.5)."""

from __future__ import annotations

import pytest

from arescode.core.state import Message, SessionState


def test_message_roundtrip():
    original = Message("user", "hi")
    assert Message.from_dict(original.to_dict()) == original


def test_append_and_to_wire():
    state = SessionState.new("m")
    state.user("hello")
    state.assistant("hi there")
    assert state.to_wire() == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]


def test_append_rejects_unknown_role():
    state = SessionState.new("m")
    with pytest.raises(ValueError):
        state.append("wizard", "nope")


def test_clear_preserves_leading_system_message():
    state = SessionState.new("m")
    state.append("system", "you are a bot")
    state.user("u")
    state.assistant("a")
    state.clear()
    assert [m.role for m in state.messages] == ["system"]


def test_clear_without_system_empties_history():
    state = SessionState.new("m")
    state.user("u")
    state.clear()
    assert state.messages == []


def test_save_and_load_roundtrip(tmp_path):
    state = SessionState.new("qwen2.5-coder:14b-instruct")
    state.user("hello")
    state.assistant("hi")
    path = state.save(tmp_path)

    assert path.exists()
    assert path.parent == tmp_path / ".arescode" / "sessions"

    loaded = SessionState.load(path)
    assert loaded.model == state.model
    assert loaded.session_id == state.session_id
    assert loaded.to_wire() == state.to_wire()


def test_load_latest_picks_newest(tmp_path):
    older = SessionState(model="m", session_id="20260101-000000")
    older.user("old")
    older.save(tmp_path)

    newer = SessionState(model="m", session_id="20260101-000001")
    newer.user("new")
    newer.save(tmp_path)

    latest = SessionState.load_latest(tmp_path)
    assert latest is not None
    assert latest.to_wire()[-1]["content"] == "new"


def test_load_latest_returns_none_when_empty(tmp_path):
    assert SessionState.load_latest(tmp_path) is None
