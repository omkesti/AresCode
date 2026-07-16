"""Master while-loop: assemble context -> model call -> parse -> execute -> append -> repeat.

Single-threaded over one flat message history (ReAct-style on top of the chat protocol): the
model emits tool actions as text, the loop executes them and feeds the results back as the next
message, until the model replies with plain text (no actions) or the step cap is hit.

Guards: a hard step cap, an interrupt flag checked between steps, and duplicate-action detection
(identical consecutive tool+args -> a nudge instead of re-running) to break the "re-read the same
file forever" pathology of weak models (context.md §4.1, TASKS 2.6).

Authored under an explicit user override of decision D10 for Phase 2. The Phase 4 permission-gate
wiring (``_permit`` plus the ``gate`` / ``approver`` parameters, per context.md §4.6) and the
Phase 5 compaction hook (the ``num_ctx`` parameter plus the ``maybe_compact`` call at the top of the
step — the summarizing logic itself lives in ``core/context.py``, per context.md §4.5) were added by
Claude Code under the author's explicit authorization to modify ``[HAND]`` files.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from typing import TYPE_CHECKING, Protocol

from arescode.core.context import maybe_compact
from arescode.core.parser import parse
from arescode.core.state import SessionState
from arescode.permissions.gate import Approval, Decision
from arescode.providers.base import ModelProvider, ProviderError
from arescode.tools.registry import (
    Action,
    EditFileAction,
    Executor,
    ToolResult,
    WriteFileAction,
    action_summary,
    format_observation,
)

if TYPE_CHECKING:
    from arescode.permissions.gate import Approver, Gate

DUPLICATE_NUDGE = (
    "You just issued the same action again — it will not give new information. Take a different "
    "step (e.g. use the exact path shown by a previous tool result), or if you already have "
    "enough information, give your final answer as plain text with no tool tags."
)

# How many times an identical action may run within one turn before it's treated as a loop.
REPEAT_LIMIT = 2

# Consecutive steps where *nothing* executed (every action skipped-as-repeat or denied) before we
# give up. Without this a model that emits the same failing action forever — e.g. a write_file whose
# content the parser couldn't recover — spins until the step cap instead of stopping promptly.
STALL_LIMIT = 2

STALL_MESSAGE = (
    "I stopped because the model kept repeating the same action without making progress "
    "(it never applied a working change). Try rephrasing the request, or check the file/edit it "
    "was attempting."
)

# Re-states the user's own request: without it a confused model will latch onto the nearest
# task-shaped text it can see — including the formatting example in the system prompt.
UNSAVED_FILE_NUDGE = (
    "You have not applied any change to a file yet — nothing has changed on disk.\n"
    "The request you are working on is, exactly: {request}\n"
    "If a file needs to change, do it now with a tool: edit_file for an existing file (a "
    "SEARCH/REPLACE block, or an empty-SEARCH block with the full new contents) or write_file for "
    "a new file. Work only on that request — never carry out the example from your instructions. "
    "Do not paste the change as text, and never write files with bash (echo >, cat >, tee, "
    "sed -i). If no change is actually needed, say so plainly."
)


def _short(text: str, limit: int = 300) -> str:
    """Collapse a request to one line for quoting back to the model."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit] + "..."

# Verbs that signal the user wants a file created or changed — used to catch a model that
# describes/pastes a change instead of applying it with a tool (a weak-model failure that gets
# worse after a conversational turn).
_CHANGE_INTENT = re.compile(
    r"\b(updat|creat|writ|edit|renam|rewrit|refactor|implement|modif|replace|add|fix|chang|"
    r"append|insert|generat)\w*",
    re.IGNORECASE,
)


def _wants_file_change(user_msg: str) -> bool:
    return bool(_CHANGE_INTENT.search(user_msg))


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


def _permit(
    action: Action,
    gate: Gate | None,
    approver: Approver | None,
    executor: Executor,
    obs: LoopObserver,
) -> ToolResult | None:
    """Apply the permission gate to one action (context.md §4.6).

    Returns None to run the action, or a denial ToolResult (fed back to the model as a tool error).
    Auto-allows read-only tools; hard-denies path escapes and blocklisted commands without ever
    prompting; and for an ASK verdict shows a change preview and routes the y/n/a answer through
    ``approver`` — remembering an "always" answer for the rest of the session.
    """
    if gate is None:
        return None
    verdict = gate.check(action)
    if verdict.decision is Decision.ALLOW:
        return None
    tool = getattr(action, "tool", "unknown")
    if verdict.decision is Decision.DENY:
        obs.notice(f"denied {tool}: {verdict.reason}")
        return ToolResult(tool, ok=False, output=f"permission denied: {verdict.reason}",
                          summary="denied")
    # ASK: preview the change, then ask the user (auto-deny if no approver is wired).
    preview = executor.preview(action)
    approval = approver(action, verdict, preview) if approver is not None else Approval(False)
    if approval.approved:
        if approval.remember:
            gate.allow_always(verdict)
        return None
    obs.notice(f"declined {tool}")
    return ToolResult(tool, ok=False,
                      output="permission denied by the user; do not retry this action",
                      summary="denied")


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
    gate: Gate | None = None,
    approver: Approver | None = None,
    num_ctx: int | None = None,
) -> str:
    """Drive one user turn to completion; returns the model's final plain-text answer.

    When ``num_ctx`` is given, the history is compacted at the top of each step once it crosses the
    budget threshold (context.md §4.5); the current task message is pinned so it is never folded.
    """
    obs = observer or NullObserver()
    task_msg = state.user(user_msg)
    last_action: Action | None = None
    executed: dict[Action, int] = {}  # per-turn execution counts, for cycle detection
    wants_change = _wants_file_change(user_msg)
    wrote_file = False
    nudged_unsaved = False
    stalled_steps = 0  # consecutive steps that executed nothing (all skipped/denied)

    for _ in range(max_steps):
        if should_interrupt():
            obs.notice("interrupted by user")
            return "Interrupted by user."

        # Keep the context within budget as a long turn accumulates tool results (context.md §4.5).
        # The task message is pinned so the model never loses sight of what it was asked to do.
        if num_ctx is not None:
            compaction = await maybe_compact(
                state, provider=provider, num_ctx=num_ctx, pin=task_msg
            )
            if compaction.compacted:
                obs.notice(
                    f"compacted history "
                    f"(~{compaction.before_tokens}->{compaction.after_tokens} tokens)"
                )

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
            # Safety net: the user asked to change a file, but the model ended its turn without
            # ever applying it (it described or pasted the change, or drifted into exploration).
            # Nudge it once to actually make the edit.
            if wants_change and not wrote_file and not nudged_unsaved:
                nudged_unsaved = True
                obs.notice("no change applied yet — asking the model to make the edit")
                state.append("user", UNSAVED_FILE_NUDGE.format(request=_short(user_msg)))
                continue
            obs.final(text)
            return text

        if result.prose:
            obs.assistant_text(result.prose)

        observations: list[str] = []
        ran_something = False  # did any action actually execute this step?
        for action in result.actions:
            # Skip an action that repeats the immediately previous one (spec) or that has
            # already run to its per-turn limit (catches A/B/A/B cycles).
            if action == last_action or executed.get(action, 0) >= REPEAT_LIMIT:
                obs.notice(f"skipped repeated {action.tool} ({action_summary(action)})")
                observations.append(f"[skipped repeated {action.tool}]\n{DUPLICATE_NUDGE}")
                continue
            # Permission gate: allow / ask-and-approve / deny — before we start the clock, so a
            # denial or the user's thinking time never counts as tool-execution latency.
            denial = _permit(action, gate, approver, executor, obs)
            if denial is not None:
                observations.append(format_observation(action, denial))
                last_action = action  # a repeat of a denied action is a no-op, not a re-prompt
                continue
            obs.tool_start(action)
            started = time.perf_counter()
            tool_result = executor.run(action)
            duration = time.perf_counter() - started
            obs.tool_end(action, tool_result, duration)
            observations.append(format_observation(action, tool_result))
            ran_something = True
            if tool_result.ok and isinstance(action, (WriteFileAction, EditFileAction)):
                wrote_file = True
            executed[action] = executed.get(action, 0) + 1
            last_action = action

        state.append("user", "\n\n".join(observations))

        # Stall guard: a step that executed nothing (every action was a skipped repeat or a
        # denial) made no progress. Tolerate a couple so the model can course-correct on the
        # nudge, then stop — otherwise a stuck model burns every remaining step doing nothing.
        stalled_steps = 0 if ran_something else stalled_steps + 1
        if stalled_steps >= STALL_LIMIT:
            obs.notice("stopping: repeated the same action without making progress")
            return STALL_MESSAGE

    obs.notice(f"reached the step limit ({max_steps})")
    return f"Reached the step limit ({max_steps} steps) without finishing. Tell me how to proceed."
