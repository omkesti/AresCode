"""Tests for context assembly, token accounting, and compaction (TASKS 5.1-5.4).

The provider is faked (returns a canned summary or raises), so no live model is needed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from arescode.core.context import (
    REPLY_RESERVE_TOKENS,
    SUMMARY_PREFIX,
    _fold,
    ares_template,
    assemble_system_prompt,
    budget_for,
    compact_now,
    estimate_tokens,
    load_project_memory,
    maybe_compact,
)
from arescode.core.state import Message, SessionState
from arescode.providers.base import Chunk, ModelProvider, ProviderError


class FakeProvider(ModelProvider):
    """Returns a canned summary; counts how many times it was called."""

    def __init__(self, summary: str = "condensed notes") -> None:
        self.summary = summary
        self.calls = 0

    async def chat(self, messages, **opts) -> AsyncIterator[Chunk]:  # type: ignore[override]
        self.calls += 1
        yield Chunk(self.summary)


class FailingProvider(ModelProvider):
    """Always fails the summarization call (to exercise the truncate fallback)."""

    async def chat(self, messages, **opts) -> AsyncIterator[Chunk]:  # type: ignore[override]
        raise ProviderError("model server down")
        yield Chunk("")  # pragma: no cover - makes this a generator


def _state(n: int, chars: int) -> SessionState:
    state = SessionState.new("m")
    for i in range(n):
        state.append("user" if i % 2 == 0 else "assistant", "x" * chars)
    return state


# --- token accounting (5.3) ------------------------------------------------


def test_budget_and_estimate():
    assert budget_for(16384) == 16384 - REPLY_RESERVE_TOKENS
    msgs = [Message("user", "x" * 400), Message("assistant", "y" * 400)]
    assert estimate_tokens(msgs) == 2 * (400 // 4)


# --- ARES.md + system prompt assembly (5.1-5.2) ----------------------------


def test_load_project_memory_present_and_absent(tmp_path):
    assert load_project_memory(tmp_path) == ""
    (tmp_path / "ARES.md").write_text("# Conventions\nUse tabs\n")
    assert "Use tabs" in load_project_memory(tmp_path)


def test_assemble_includes_memory_and_repo_map(tmp_path):
    (tmp_path / "ARES.md").write_text("PROJECT RULES\n")
    (tmp_path / "a.py").write_text("x = 1\n")

    prompt = assemble_system_prompt(tmp_path, base_prompt="BASE")

    assert "BASE" in prompt
    assert "PROJECT RULES" in prompt
    assert "Repository map" in prompt
    assert "a.py" in prompt


def test_assemble_omits_absent_sections(tmp_path):
    # No ARES.md, empty repo map -> the prompt is just the base.
    assert assemble_system_prompt(tmp_path, base_prompt="BASE", memory="", repo_map="") == "BASE"


def test_ares_template_scaffold(tmp_path):
    text = ares_template(tmp_path)
    assert "ARES.md" in text
    assert "Key commands" in text
    assert tmp_path.name in text


# --- compaction pure helper (5.4) ------------------------------------------


def test_fold_replaces_compactable_range_with_summary():
    msgs = [Message("user", "a"), Message("assistant", "b"),
            Message("user", "c"), Message("assistant", "d")]
    out = _fold(msgs, [0, 1], Message("assistant", "S"))
    assert [m.content for m in out] == ["S", "c", "d"]


# --- compaction orchestration (5.4) ----------------------------------------


async def test_maybe_compact_is_noop_under_threshold():
    state = _state(2, 40)  # trivially small
    result = await maybe_compact(state, provider=FakeProvider(), num_ctx=16384)
    assert not result.compacted
    assert result.method == "none"


async def test_maybe_compact_folds_over_threshold():
    state = _state(20, 4000)  # ~20000 tokens, far over 75% of an 8k window
    before = len(state.messages)
    provider = FakeProvider("condensed")

    result = await maybe_compact(state, provider=provider, num_ctx=8192)

    assert result.compacted
    assert result.method == "summary"
    assert provider.calls == 1
    assert state.messages[0].content.startswith(SUMMARY_PREFIX)
    assert len(state.messages) < before
    assert estimate_tokens(state.messages) <= budget_for(8192)


async def test_compaction_pins_task_message():
    state = _state(20, 4000)
    pin = state.messages[0]  # pretend the first message is the live task

    result = await maybe_compact(state, provider=FakeProvider(), num_ctx=8192, pin=pin)

    assert result.compacted
    assert any(m is pin for m in state.messages)  # the pinned task survived (by identity)


async def test_compaction_preserves_recent_tail():
    state = _state(20, 4000)
    last_four = state.messages[-4:]

    await maybe_compact(state, provider=FakeProvider(), num_ctx=8192)

    for message in last_four:
        assert any(m is message for m in state.messages)


async def test_compaction_falls_back_to_truncate_on_provider_error():
    state = _state(20, 4000)
    before = len(state.messages)

    result = await maybe_compact(state, provider=FailingProvider(), num_ctx=8192)

    assert result.method == "truncate"
    assert result.compacted
    assert len(state.messages) < before


async def test_compact_now_forces_even_under_threshold():
    state = _state(10, 400)  # small; well under threshold
    provider = FakeProvider("s")

    result = await compact_now(state, provider=provider, num_ctx=16384)

    assert result.compacted
    assert result.method == "summary"
    assert provider.calls == 1
    assert state.messages[0].content.startswith(SUMMARY_PREFIX)
