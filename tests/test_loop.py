"""Tests for the master loop (TASKS 2.6) using a scripted fake provider + executor."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import nullcontext

from arescode.config import Config
from arescode.core.loop import run_turn
from arescode.core.state import SessionState
from arescode.permissions.gate import Approval, Gate
from arescode.providers.base import Chunk, ModelProvider
from arescode.tools.registry import Executor, ReadFileAction, ToolResult


class ScriptedProvider(ModelProvider):
    """Returns each scripted completion in turn (as a single streamed chunk)."""

    def __init__(self, scripted: list[str]) -> None:
        self.scripted = scripted
        self.calls = 0

    async def chat(self, messages, **opts) -> AsyncIterator[Chunk]:  # type: ignore[override]
        text = self.scripted[min(self.calls, len(self.scripted) - 1)]
        self.calls += 1
        yield Chunk(text)


class LoopingProvider(ModelProvider):
    """Never finishes: emits a distinct read action every call (to exercise the step cap)."""

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages, **opts) -> AsyncIterator[Chunk]:  # type: ignore[override]
        i = self.calls
        self.calls += 1
        yield Chunk(f"<tool>read_file</tool><path>f{i}.py</path>")


class FakeExecutor:
    def __init__(self) -> None:
        self.calls: list = []

    def run(self, action):
        self.calls.append(action)
        return ToolResult(action.tool, True, "result", summary="ok")


class RecordingObserver:
    def __init__(self) -> None:
        self.notices: list[str] = []
        self.finals: list[str] = []
        self.tools: list = []

    def thinking(self):
        return nullcontext()

    def assistant_text(self, text): ...
    def tool_start(self, action): ...
    def tool_end(self, action, result, duration):
        self.tools.append(action)

    def final(self, text):
        self.finals.append(text)

    def notice(self, text):
        self.notices.append(text)

    def error(self, text): ...


async def test_loop_executes_then_finishes():
    provider = ScriptedProvider([
        "<tool>read_file</tool><path>a.py</path>",
        "The answer is 42.",
    ])
    executor = FakeExecutor()
    state = SessionState.new("m")
    obs = RecordingObserver()

    result = await run_turn(
        "hi", state=state, provider=provider, executor=executor,
        system_prompt="sys", observer=obs, max_steps=5,
    )

    assert result == "The answer is 42."
    assert executor.calls == [ReadFileAction("a.py")]
    assert obs.finals == ["The answer is 42."]
    assert [m.role for m in state.messages] == ["user", "assistant", "user", "assistant"]


async def test_loop_hits_step_cap():
    executor = FakeExecutor()
    state = SessionState.new("m")
    result = await run_turn(
        "go", state=state, provider=LoopingProvider(), executor=executor,
        system_prompt="sys", max_steps=3,
    )
    assert "step limit" in result
    assert len(executor.calls) == 3


async def test_loop_skips_duplicate_action():
    provider = ScriptedProvider([
        "<tool>read_file</tool><path>a.py</path>",
        "<tool>read_file</tool><path>a.py</path>",  # identical -> should be skipped
        "done",
    ])
    executor = FakeExecutor()
    obs = RecordingObserver()
    result = await run_turn(
        "hi", state=SessionState.new("m"), provider=provider, executor=executor,
        system_prompt="sys", observer=obs, max_steps=5,
    )
    assert result == "done"
    assert executor.calls == [ReadFileAction("a.py")]  # executed once, not twice
    assert any("skipped repeated" in n for n in obs.notices)


class StuckProvider(ModelProvider):
    """Always emits the SAME action — models the empty-write_file spin from the bug report."""

    def __init__(self, action_text: str) -> None:
        self.action_text = action_text
        self.calls = 0

    async def chat(self, messages, **opts) -> AsyncIterator[Chunk]:  # type: ignore[override]
        self.calls += 1
        yield Chunk(self.action_text)


async def test_loop_stops_when_action_repeats_without_progress():
    # Regression: an identical action every step used to be skipped-as-duplicate forever, burning
    # all 25 steps. It must now stop promptly once no step makes progress.
    provider = StuckProvider("<tool>write_file</tool><path>x.py</path>\n```\ncode\n```")
    executor = FakeExecutor()
    obs = RecordingObserver()
    result = await run_turn(
        "go", state=SessionState.new("m"), provider=provider, executor=executor,
        system_prompt="sys", observer=obs, max_steps=25,
    )
    assert "without making progress" in result
    assert len(executor.calls) == 1  # ran once, then only skipped repeats
    assert provider.calls < 25  # stopped well before the step cap
    assert any("stopping" in n for n in obs.notices)


async def test_loop_compacts_when_history_over_budget():
    # A long pre-existing history + num_ctx -> the loop compacts at the top of the step. The same
    # scripted provider answers the summarization call ("summary") and then the turn ("done").
    state = SessionState.new("m")
    for i in range(30):
        state.append("user" if i % 2 == 0 else "assistant", "x" * 4000)  # ~30000 tokens
    provider = ScriptedProvider(["condensed summary", "done"])
    obs = RecordingObserver()

    result = await run_turn(
        "finish up", state=state, provider=provider, executor=FakeExecutor(),
        system_prompt="sys", observer=obs, max_steps=5, num_ctx=8192,
    )

    assert result == "done"
    assert any("compacted history" in n for n in obs.notices)
    assert any(m.content.startswith("Summary of earlier work:") for m in state.messages)


async def test_loop_without_num_ctx_never_compacts():
    # Backwards-compatible default: no num_ctx -> no compaction call, even with a big history.
    state = SessionState.new("m")
    for i in range(30):
        state.append("user" if i % 2 == 0 else "assistant", "x" * 4000)
    provider = ScriptedProvider(["done"])  # a single completion; no summarization call consumed
    result = await run_turn(
        "hi", state=state, provider=provider, executor=FakeExecutor(),
        system_prompt="sys", max_steps=5,
    )
    assert result == "done"
    assert provider.calls == 1  # only the turn call; compaction never ran


async def test_loop_respects_interrupt_flag():
    provider = ScriptedProvider(["should not be called"])
    result = await run_turn(
        "hi", state=SessionState.new("m"), provider=provider, executor=FakeExecutor(),
        system_prompt="sys", should_interrupt=lambda: True,
    )
    assert "Interrupted" in result
    assert provider.calls == 0


async def test_loop_nudges_when_file_content_not_saved():
    # Model dumps the file in a code fence (no tool) -> loop nudges -> model applies write_file.
    provider = ScriptedProvider([
        "Here is the updated file:\n```\nnew content\n```",
        "<tool>write_file</tool><path>foo.txt</path>\n```\nnew content\n```",
        "Done.",
    ])
    executor = FakeExecutor()
    obs = RecordingObserver()
    result = await run_turn(
        "Please update foo.txt with new content", state=SessionState.new("m"),
        provider=provider, executor=executor, system_prompt="sys", observer=obs, max_steps=5,
    )
    assert result == "Done."
    assert any(a.tool == "write_file" for a in executor.calls)
    assert any("no change applied" in n for n in obs.notices)


async def test_nudge_restates_the_original_request():
    # Guard: the nudge must quote the user's actual request. Without it a confused model latches
    # onto the nearest task-shaped text — e.g. the formatting example in the system prompt.
    provider = ScriptedProvider([
        "I would update it like this:\n```\nnew content\n```",  # no tool -> triggers the nudge
        "Done.",
    ])
    state = SessionState.new("m")
    await run_turn(
        "Please update README.md to describe the project",
        state=state, provider=provider, executor=FakeExecutor(),
        system_prompt="sys", max_steps=5,
    )
    nudges = [m.content for m in state.messages if "not applied any change" in m.content]
    assert nudges, "expected the loop to nudge"
    assert "update README.md to describe the project" in nudges[0]
    assert "never carry out the example" in nudges[0]


async def test_loop_does_not_nudge_a_pure_question():
    # A question that just happens to include a code fence must not trigger the write nudge.
    provider = ScriptedProvider(["Here is how it works:\n```\nsome code\n```"])
    obs = RecordingObserver()
    result = await run_turn(
        "What does foo do? Show me.", state=SessionState.new("m"),
        provider=provider, executor=FakeExecutor(), system_prompt="sys", observer=obs, max_steps=5,
    )
    assert result.startswith("Here is how it works")
    assert not any("no change applied" in n for n in obs.notices)


# --- permission gate inside the loop (TASKS 4.1) ---------------------------

def _bash_provider(cmds: list[str]) -> ScriptedProvider:
    """A provider that emits each bash command in turn, then finishes with plain text."""
    return ScriptedProvider([f"<tool>bash</tool><cmd>{c}</cmd>" for c in cmds] + ["done"])


async def test_loop_runs_action_when_approved(tmp_path):
    gate = Gate(tmp_path)
    obs = RecordingObserver()
    result = await run_turn(
        "hi", state=SessionState.new("m"), provider=_bash_provider(["echo hi"]),
        executor=Executor(tmp_path, Config(), gate=gate), system_prompt="s", observer=obs,
        gate=gate, approver=lambda a, v, p: Approval(True), max_steps=5,
    )
    assert result == "done"
    assert len(obs.tools) == 1  # the approved bash action executed


async def test_loop_skips_declined_action(tmp_path):
    gate = Gate(tmp_path)
    obs = RecordingObserver()
    state = SessionState.new("m")
    result = await run_turn(
        "hi", state=state, provider=_bash_provider(["echo hi"]),
        executor=Executor(tmp_path, Config(), gate=gate), system_prompt="s", observer=obs,
        gate=gate, approver=lambda a, v, p: Approval(False), max_steps=5,
    )
    assert result == "done"
    assert obs.tools == []  # never executed
    assert any("declined" in n for n in obs.notices)
    assert any("denied by the user" in m.content for m in state.messages if m.role == "user")


async def test_loop_hard_denies_blocklisted_without_prompting(tmp_path):
    gate = Gate(tmp_path)
    obs = RecordingObserver()
    state = SessionState.new("m")
    prompted = {"n": 0}

    def approver(action, verdict, preview):
        prompted["n"] += 1
        return Approval(True)

    result = await run_turn(
        "hi", state=state, provider=_bash_provider(["sudo rm -rf /"]),
        executor=Executor(tmp_path, Config(), gate=gate), system_prompt="s", observer=obs,
        gate=gate, approver=approver, max_steps=5,
    )
    assert result == "done"
    assert prompted["n"] == 0  # a hard deny never reaches the user
    assert obs.tools == []
    assert any("blocked command" in m.content for m in state.messages if m.role == "user")


async def test_loop_remembers_always_answer(tmp_path):
    gate = Gate(tmp_path)
    obs = RecordingObserver()
    prompted = {"n": 0}

    def approver(action, verdict, preview):
        prompted["n"] += 1
        return Approval(True, remember=True)

    result = await run_turn(
        "hi", state=SessionState.new("m"), provider=_bash_provider(["echo a", "echo b"]),
        executor=Executor(tmp_path, Config(), gate=gate), system_prompt="s", observer=obs,
        gate=gate, approver=approver, max_steps=6,
    )
    assert result == "done"
    assert prompted["n"] == 1  # second 'echo' auto-allowed by the remembered token
    assert "echo" in gate.session_commands
    assert len(obs.tools) == 2


async def test_loop_without_gate_runs_freely(tmp_path):
    obs = RecordingObserver()
    result = await run_turn(
        "hi", state=SessionState.new("m"), provider=_bash_provider(["echo hi"]),
        executor=Executor(tmp_path, Config()), system_prompt="s", observer=obs, max_steps=5,
    )
    assert result == "done"
    assert len(obs.tools) == 1
