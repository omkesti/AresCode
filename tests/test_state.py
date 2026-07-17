"""Tests for the session state / history model (TASKS 1.5)."""

from __future__ import annotations

import pytest

from arescode.core.state import Message, SessionInfo, SessionState


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


# --- /sessions listing + /resume <id> resolution (TASKS 6.1) -----------------


def _seed(tmp_path, session_id: str, model: str, messages: int) -> None:
    state = SessionState(model=model, session_id=session_id)
    for i in range(messages):
        state.user(f"m{i}")
    state.save(tmp_path)


def test_list_sessions_newest_first_with_metadata(tmp_path):
    _seed(tmp_path, "20260101-000000", "m-old", 2)
    _seed(tmp_path, "20260102-000000", "m-new", 4)

    infos = SessionState.list_sessions(tmp_path)
    assert [i.session_id for i in infos] == ["20260102-000000", "20260101-000000"]
    assert isinstance(infos[0], SessionInfo)
    assert infos[0].model == "m-new"
    assert infos[0].message_count == 4
    assert infos[1].message_count == 2


def test_list_sessions_empty_when_no_dir(tmp_path):
    assert SessionState.list_sessions(tmp_path) == []


def test_list_sessions_skips_malformed_files(tmp_path):
    _seed(tmp_path, "20260101-000000", "m", 1)
    (tmp_path / ".arescode" / "sessions" / "broken.json").write_text("{not json")
    infos = SessionState.list_sessions(tmp_path)
    assert [i.session_id for i in infos] == ["20260101-000000"]  # broken one is skipped


def test_resolve_exact_id(tmp_path):
    _seed(tmp_path, "20260101-000000", "m", 3)
    state, err = SessionState.resolve(tmp_path, "20260101-000000")
    assert err is None
    assert state is not None
    assert len(state.messages) == 3


def test_resolve_unique_prefix(tmp_path):
    _seed(tmp_path, "20260101-120000", "m", 1)
    _seed(tmp_path, "20260202-120000", "m", 1)
    state, err = SessionState.resolve(tmp_path, "20260101")
    assert err is None
    assert state is not None and state.session_id == "20260101-120000"


def test_resolve_ambiguous_prefix_reports_candidates(tmp_path):
    _seed(tmp_path, "20260101-120000", "m", 1)
    _seed(tmp_path, "20260101-130000", "m", 1)
    state, err = SessionState.resolve(tmp_path, "20260101")
    assert state is None
    assert err is not None and "matches 2 sessions" in err


def test_resolve_no_match(tmp_path):
    _seed(tmp_path, "20260101-120000", "m", 1)
    state, err = SessionState.resolve(tmp_path, "19990101")
    assert state is None
    assert err is not None and "no session matches" in err


def test_resolve_empty_ref_and_no_sessions(tmp_path):
    state, err = SessionState.resolve(tmp_path, "  ")
    assert state is None and err == "usage: /resume <session-id>"
    state, err = SessionState.resolve(tmp_path, "anything")
    assert state is None and err == "no saved sessions to resume"
