"""Context assembly, token budget, and compaction — the endurance layer (context.md §4.5).

Three jobs, all so a weak local model stays coherent as a session grows:

1. **System-prompt assembly** — the versioned base prompt (`prompts/system.md`) plus, when present,
   the project's ``ARES.md`` memory and a repo map, joined once at session start (TASKS 5.1-5.2).
2. **Token accounting** — a cheap ``len // 4`` estimate per message and a usable budget of
   ``num_ctx`` minus a reply reserve (TASKS 5.3). This module owns those primitives;
   :mod:`arescode.core.models` re-exports them for the switch path.
3. **Compaction** — when the history crosses 75% of the budget, the oldest turns are folded into a
   single summarizing assistant message via a dedicated model call, protecting the current task
   statement and the most recent tool results (TASKS 5.4). If the summarization call fails we fall
   back to a lossy hard-truncation so the session still fits.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from arescode.core.state import Message
from arescode.providers.base import ProviderError
from arescode.repo.repomap import build_repo_map

if TYPE_CHECKING:
    from arescode.core.state import SessionState
    from arescode.providers.base import ModelProvider

# Minimal fallback if prompts/system.md can't be located (e.g. an unusual install layout).
_FALLBACK_SYSTEM_PROMPT = (
    "You are AresCode, a coding agent. Use tools with <tool>name</tool><path>...</path> style "
    "tags (read_file, grep, glob, bash) to explore and act, then reply in plain text with no "
    "tool tags to finish."
)

ARES_MEMORY_FILENAME = "ARES.md"

# --- token budget -----------------------------------------------------------
# Tokens held back from the window for the model's own reply (context.md §4.5).
REPLY_RESERVE_TOKENS = 1500
# Fraction of the usable budget at which auto-compaction fires.
COMPACT_THRESHOLD = 0.75
# Messages at the tail always protected from compaction: the live task + recent tool results.
KEEP_RECENT_MESSAGES = 4
# Below this many compactable messages, folding isn't worth a summarization call.
MIN_COMPACTABLE = 2


def estimate_tokens(messages: list[Message]) -> int:
    """Rough token count over a message list (``len // 4``, per context.md §4.5)."""
    return sum(len(m.content) // 4 for m in messages)


def budget_for(num_ctx: int, reserve: int = REPLY_RESERVE_TOKENS) -> int:
    """Usable history budget = context window minus the reply reserve."""
    return max(1, num_ctx - reserve)


def hard_truncate(messages: list[Message], budget: int, *, keep_last: int = 4) -> int:
    """Drop oldest whole messages until the history fits ``budget``; returns how many were removed.

    A lossy last resort used when a summarization call is impossible (a shrinking-window model
    switch, or a failed/empty summary). ``keep_last`` protects the most recent turns.
    """
    removed = 0
    while len(messages) > keep_last and estimate_tokens(messages) > budget:
        del messages[0]
        removed += 1
    return removed


# ---------------------------------------------------------------------------
# System prompt + project memory (TASKS 5.1-5.2)
# ---------------------------------------------------------------------------


def _system_prompt_candidates() -> list[Path]:
    """Every place the versioned system prompt might live, best (nearest) first.

    Two layouts must both work: an *installed* package (``pipx install`` / a wheel), where the
    prompt is bundled beside the package as ``arescode/prompts/system.md`` (via the wheel
    force-include in pyproject.toml), and a *source tree* (editable install), where it lives at the
    repo root in ``prompts/system.md``. Shipping only the latter path was a real bug — an installed
    AresCode silently fell back to the minimal prompt, dropping the whole action protocol (6.4).
    """
    here = Path(__file__).resolve()
    return [
        here.parents[1] / "prompts" / "system.md",  # installed: <site-packages>/arescode/prompts/
        here.parents[3] / "prompts" / "system.md",  # source tree: <repo>/prompts/
    ]


@lru_cache(maxsize=1)
def load_system_prompt() -> str:
    """Return the versioned system prompt, or a minimal fallback if it can't be found."""
    for path in _system_prompt_candidates():
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text
    return _FALLBACK_SYSTEM_PROMPT


def load_project_memory(project_dir: Path | str) -> str:
    """Return the contents of the project's ``ARES.md``, or ``""`` if it is absent/unreadable."""
    path = Path(project_dir) / ARES_MEMORY_FILENAME
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def assemble_system_prompt(
    project_dir: Path | str,
    *,
    base_prompt: str | None = None,
    memory: str | None = None,
    repo_map: str | None = None,
) -> str:
    """Compose the full system prompt: base + ARES.md memory + repo map.

    ``memory`` / ``repo_map`` may be passed in when the caller has already built them (the REPL
    keeps the repo map around for ``/map``); otherwise they are loaded/built here. Empty sections
    are omitted so a project without an ``ARES.md`` or files adds nothing.
    """
    base = load_system_prompt() if base_prompt is None else base_prompt
    memory = load_project_memory(project_dir) if memory is None else memory
    repo_map = build_repo_map(project_dir) if repo_map is None else repo_map

    parts = [base]
    if memory:
        parts.append(f"# Project memory (ARES.md)\n\n{memory}")
    if repo_map:
        parts.append(
            "# Repository map\n\n"
            "Files in this project (sizes shown; gitignored paths omitted). Use exact paths.\n\n"
            f"{repo_map}"
        )
    return "\n\n".join(parts)


# --- /init: model authors the content, harness gathers + persists it (context.md §4.5) ------------
# Weak local models reliably WRITE prose but do NOT reliably EMIT a write_file tool call across an
# exploration loop (both 7B and 14B, given the agentic version, explored then drifted into
# summarizing instead of writing). So /init is deliberately NOT an agent turn: the harness reads the
# orientation files and the model produces the file's Markdown in a single completion, which the
# REPL then writes through the normal gated path. This is the project's "fix the harness, not the
# prompt" rule applied literally — the model only does the part it's good at.

INIT_SYSTEM_PROMPT = (
    f"You are a precise technical writer producing {ARES_MEMORY_FILENAME}, a short project-memory "
    "file that an AI coding agent loads into its context at the start of every session. You write "
    "terse, accurate, high-signal GitHub-flavored Markdown grounded strictly in the material you "
    "are given. You output ONLY the file's contents — no preamble, no sign-off, no wrapping code "
    "fence, no tool calls."
)

INIT_USER_TEMPLATE = f"""\
Write the contents of {ARES_MEMORY_FILENAME} for this project. Base it ONLY on the repository \
material below — do not invent commands, paths, or facts it does not support.

{{context}}

Produce the {ARES_MEMORY_FILENAME} content now, as Markdown, with these sections (omit any the \
material does not support):
- Overview: one paragraph on what the project is and its entry point.
- Where things are: the key directories/files and what each is for.
- Key commands: install, run, test, lint/format — the real commands.
- Conventions: language/framework/style; where new code and tests go.
- Notes: gotchas, required env vars, services that must be running.

Keep it under ~60 lines. Output ONLY the file content — no preamble, no explanation, no code fence.
"""

# Orientation files worth reading when they exist; the model sees these plus the repo map.
_INIT_CONTEXT_FILES = (
    "README.md", "README.rst", "README.txt",
    "pyproject.toml", "setup.cfg", "setup.py", "package.json",
    "go.mod", "Cargo.toml", "Makefile", "CONTRIBUTING.md",
)
_INIT_FILE_MAX_LINES = 160  # per-file head cap so one big README can't blow the window


def gather_init_context(project_dir: Path | str, *, repo_map: str = "") -> str:
    """Collect the orientation the ``/init`` writer turn reads: the repo map + key project files.

    The harness does the exploring (reliable) so the model only has to synthesize (what it is good
    at). Each file is head-truncated; absent files are skipped.
    """
    root = Path(project_dir)
    parts: list[str] = []
    if repo_map:
        parts.append(f"### Repository structure (gitignore-filtered)\n\n{repo_map}")
    seen: set[Path] = set()
    for name in _INIT_CONTEXT_FILES:
        path = root / name
        resolved = path.resolve()
        if resolved in seen:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        seen.add(resolved)
        lines = text.splitlines()
        if len(lines) > _INIT_FILE_MAX_LINES:
            omitted = len(lines) - _INIT_FILE_MAX_LINES
            text = "\n".join(lines[:_INIT_FILE_MAX_LINES]) + f"\n... [{omitted} more lines]"
        parts.append(f"### {name}\n\n{text}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Compaction (TASKS 5.4)
# ---------------------------------------------------------------------------

SUMMARY_PREFIX = "Summary of earlier work:"

_SUMMARIZE_SYSTEM_PROMPT = (
    "You compress a coding agent's conversation history. Rewrite the transcript excerpt below as a "
    "compact set of notes the agent can rely on to keep working. Preserve: the user's goal, "
    "decisions made, files read or changed and how, commands run and their outcomes, and any fact "
    "still needed later. Drop pleasantries and verbose tool output. Write terse notes, not prose. "
    "Invent nothing that is not in the excerpt."
)


@dataclass(slots=True)
class CompactionResult:
    """Outcome of a compaction pass (for the UI indicator and tests)."""

    compacted: bool
    folded: int = 0  # messages folded into the summary (or dropped, when method == "truncate")
    before_tokens: int = 0
    after_tokens: int = 0
    method: str = "none"  # "summary" | "truncate" | "none"
    summary: str = ""
    reason: str = ""


def _protected_indices(messages: list[Message], keep_recent: int, pin: Message | None) -> set[int]:
    """Indices never folded: the last ``keep_recent`` messages plus the pinned task."""
    n = len(messages)
    protected = set(range(max(0, n - keep_recent), n))
    if pin is not None:
        protected.update(i for i, m in enumerate(messages) if m is pin)
    return protected


def _fold(messages: list[Message], compactable: list[int], summary: Message) -> list[Message]:
    """Replace the ``compactable`` messages with a single ``summary`` at the earliest such slot."""
    compact_set = set(compactable)
    first = compactable[0]
    out: list[Message] = []
    for i, message in enumerate(messages):
        if i in compact_set:
            if i == first:
                out.append(summary)
            continue
        out.append(message)
    return out


def _render_transcript(messages: list[Message]) -> str:
    return "\n\n".join(f"{m.role.upper()}: {m.content}" for m in messages)


async def summarize_messages(provider: ModelProvider, messages: list[Message]) -> str:
    """Ask the model to condense ``messages`` into terse notes; raises ProviderError on failure."""
    prompt = f"Summarize this transcript excerpt:\n\n{_render_transcript(messages)}"
    wire = [
        {"role": "system", "content": _SUMMARIZE_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    return (await provider.complete(wire)).strip()


async def _compact(
    state: SessionState,
    *,
    provider: ModelProvider,
    num_ctx: int,
    force: bool,
    pin: Message | None,
    keep_recent: int,
    threshold: float,
) -> CompactionResult:
    messages = state.messages
    budget = budget_for(num_ctx)
    before = estimate_tokens(messages)

    if not force and before <= int(threshold * budget):
        return CompactionResult(False, before_tokens=before, after_tokens=before,
                                reason="under threshold")

    protected = _protected_indices(messages, keep_recent, pin)
    compactable = [i for i in range(len(messages)) if i not in protected]
    if len(compactable) < MIN_COMPACTABLE:
        # Not enough foldable history. If we're actually over the hard budget, shed oldest anyway.
        if before > budget:
            return _truncate_result(messages, budget, keep_recent, before, "too little to fold")
        return CompactionResult(False, before_tokens=before, after_tokens=before,
                                reason="too little to compact")

    to_summarize = [messages[i] for i in compactable]
    try:
        summary_text = await summarize_messages(provider, to_summarize)
    except ProviderError as exc:
        reason = f"summarization failed: {exc}"
        return _truncate_result(messages, budget, keep_recent, before, reason)
    if not summary_text:
        return _truncate_result(messages, budget, keep_recent, before, "empty summary")

    summary = Message("assistant", f"{SUMMARY_PREFIX}\n{summary_text}")
    state.messages = _fold(messages, compactable, summary)
    # Safety net: if the summary + protected tail still overflow, shed the oldest as a last resort.
    if estimate_tokens(state.messages) > budget:
        hard_truncate(state.messages, budget, keep_last=keep_recent)
    after = estimate_tokens(state.messages)
    return CompactionResult(True, folded=len(compactable), before_tokens=before, after_tokens=after,
                            method="summary", summary=summary_text)


def _truncate_result(
    messages: list[Message], budget: int, keep_recent: int, before: int, reason: str
) -> CompactionResult:
    removed = hard_truncate(messages, budget, keep_last=keep_recent)
    after = estimate_tokens(messages)
    return CompactionResult(bool(removed), folded=removed, before_tokens=before, after_tokens=after,
                            method="truncate" if removed else "none", reason=reason)


async def maybe_compact(
    state: SessionState,
    *,
    provider: ModelProvider,
    num_ctx: int,
    pin: Message | None = None,
    keep_recent: int = KEEP_RECENT_MESSAGES,
    threshold: float = COMPACT_THRESHOLD,
) -> CompactionResult:
    """Compact only if the history has crossed ``threshold`` of the budget (the loop auto path)."""
    return await _compact(state, provider=provider, num_ctx=num_ctx, force=False, pin=pin,
                          keep_recent=keep_recent, threshold=threshold)


async def compact_now(
    state: SessionState,
    *,
    provider: ModelProvider,
    num_ctx: int,
    keep_recent: int = KEEP_RECENT_MESSAGES,
) -> CompactionResult:
    """Force a compaction regardless of the current budget (the ``/compact`` command)."""
    return await _compact(state, provider=provider, num_ctx=num_ctx, force=True, pin=None,
                          keep_recent=keep_recent, threshold=COMPACT_THRESHOLD)
