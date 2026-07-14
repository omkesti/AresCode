"""Master while-loop: assemble context -> model call -> parse -> execute -> append -> repeat.

Single-threaded over one flat message history (ReAct-style on top of the chat protocol): the
model emits tool actions as text, the loop executes them and feeds the results back as the next
message, until the model replies with plain text (no actions) or the step cap is hit.

Guards: a hard step cap, an interrupt flag checked between steps, and duplicate-action detection
(identical consecutive tool+args -> a nudge instead of re-running) to break the "re-read the same
file forever" pathology of weak models (context.md §4.1, TASKS 2.6).

Authored under an explicit user override of decision D10 for Phase 2.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from typing import Protocol

from arescode.core.parser import parse
from arescode.core.state import SessionState
from arescode.providers.base import ModelProvider, ProviderError
from arescode.tools.registry import Action, Executor, ToolResult, action_summary, format_observation

DUPLICATE_NUDGE = (
    "You just issued the same action again — it will not give new information. Take a different "
    "step (e.g. use the exact path shown by a previous tool result), or if you already have "
    "enough information, give your final answer as plain text with no tool tags."
)

# How many times an identical action may run within one turn before it's treated as a loop.
REPEAT_LIMIT = 2


class LoopObserver(Protocol):
    """UI hooks the loop calls as it runs. See ui/render.ConsoleObserver for the real one."""

    def thinking(self) -> AbstractContextManager: ...
    def assistant_text(self, text: str) -> None: ...
    def tool_start(self, action: Action) -> None: ...
    def tool_end(self, action: Action, result: ToolResult, duration: float) -> None: ...
    def final(self, text: str) -> None: ...
    def notice(self, text: str) -> None: ...
    def error(self, text: str) -> None: ...


class NullObserver:
    """No-op observer (used by tests and headless runs)."""

    def thinking(self) -> AbstractContextManager:
        return nullcontext()

    def assistant_text(self, text: str) -> None: ...
    def tool_start(self, action: Action) -> None: ...
    def tool_end(self, action: Action, result: ToolResult, duration: float) -> None: ...
    def final(self, text: str) -> None: ...
    def notice(self, text: str) -> None: ...
    def error(self, text: str) -> None: ...


async def run_turn(
    user_msg: str,
    *,
    state: SessionState,
    provider: ModelProvider,
    executor: Executor,
    system_prompt: str,
    observer: LoopObserver | None = None,
    max_steps: int = 25,
    should_interrupt: Callable[[], bool] = lambda: False,
) -> str:
    """Drive one user turn to completion; returns the model's final plain-text answer."""
    obs = observer or NullObserver()
    state.user(user_msg)
    last_action: Action | None = None
    executed: dict[Action, int] = {}  # per-turn execution counts, for cycle detection

    for _ in range(max_steps):
        if should_interrupt():
            obs.notice("interrupted by user")
            return "Interrupted by user."

        messages = [{"role": "system", "content": system_prompt}, *state.to_wire()]
        try:
            with obs.thinking():
                text = await provider.complete(messages)
        except ProviderError as exc:
            obs.error(str(exc))
            return f"error: {exc}"

        state.assistant(text)
        result = parse(text)

        if not result.actions:  # plain text -> the turn is done
            obs.final(text)
            return text

        if result.prose:
            obs.assistant_text(result.prose)

        observations: list[str] = []
        for action in result.actions:
            # Skip an action that repeats the immediately previous one (spec) or that has
            # already run to its per-turn limit (catches A/B/A/B cycles).
            if action == last_action or executed.get(action, 0) >= REPEAT_LIMIT:
                obs.notice(f"skipped repeated {action.tool} ({action_summary(action)})")
                observations.append(f"[skipped repeated {action.tool}]\n{DUPLICATE_NUDGE}")
                continue
            obs.tool_start(action)
            started = time.perf_counter()
            tool_result = executor.run(action)
            duration = time.perf_counter() - started
            obs.tool_end(action, tool_result, duration)
            observations.append(format_observation(action, tool_result))
            executed[action] = executed.get(action, 0) + 1
            last_action = action

        state.append("user", "\n\n".join(observations))

    obs.notice(f"reached the step limit ({max_steps})")
    return f"Reached the step limit ({max_steps} steps) without finishing. Tell me how to proceed."
