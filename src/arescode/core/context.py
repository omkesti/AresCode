"""Context assembly and token budget.

Phase 2 provides only the system-prompt loader. Full context management — repo map + ARES.md
injection, ``len // 4`` token estimation, and 75%-budget compaction (never compacting the system
prompt, the current task, or the last four tool results) — arrives in Phase 5
(context.md §4.5, TASKS 5.3-5.4).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

# Minimal fallback if prompts/system.md can't be located (e.g. an unusual install layout).
_FALLBACK_SYSTEM_PROMPT = (
    "You are AresCode, a coding agent. Use tools with <tool>name</tool><path>...</path> style "
    "tags (read_file, grep, glob, bash) to explore and act, then reply in plain text with no "
    "tool tags to finish."
)


def _system_prompt_path() -> Path:
    # This file lives at <repo>/src/arescode/core/context.py; the prompt at <repo>/prompts/.
    return Path(__file__).resolve().parents[3] / "prompts" / "system.md"


@lru_cache(maxsize=1)
def load_system_prompt() -> str:
    """Return the versioned system prompt, or a minimal fallback if it can't be found."""
    path = _system_prompt_path()
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return _FALLBACK_SYSTEM_PROMPT
    return text or _FALLBACK_SYSTEM_PROMPT
