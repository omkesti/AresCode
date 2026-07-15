"""ModelManager: the safe mid-session model-switch lifecycle (D12).

A switch on a small-VRAM box must be *exclusive* — the old model has to leave VRAM before the new
one loads, or a 6GB GPU thrashes to CPU. :meth:`ModelManager.switch` runs that lifecycle:

    validate the target is installed
    -> refuse if the agent is mid-turn (REPL-idle only)
    -> unload the current model via the native admin API (best-effort; a failure never aborts)
    -> warm up the target (so the next chat call serves it without a reload)
    -> update session state + tag edit telemetry with the new model
    -> recompute the token budget for the new num_ctx and compact if history no longer fits

The chat path is untouched (still OpenAI-compat); everything native goes through the injected
:class:`~arescode.providers.ollama_admin.OllamaAdmin`. When that admin is unavailable the switch
*degrades*: it changes the model name only, and unload/warmup/compaction-by-VRAM become no-ops.

Compaction note: Phase 5's summarizing compaction (TASKS 5.4) is not built yet, so when the new,
smaller window can't hold the history this module falls back to :func:`hard_truncate` — dropping
the oldest whole messages with a visible warning. That is a deliberate stopgap; the ``compact``
seam lets Phase 5 replace it with real summarization without touching this file's callers.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from arescode.providers.ollama_admin import AdminUnavailable, InstalledModel, OllamaAdmin

if TYPE_CHECKING:
    from arescode.config import Config
    from arescode.core.state import Message, SessionState
    from arescode.tools.registry import Executor

# Tokens held back from the window for the model's own reply (context.md §4.5).
REPLY_RESERVE_TOKENS = 1500

# Sentinel: "fetch the installed list yourself". Distinct from None ("admin unavailable").
_UNSET: object = object()

ProgressFn = Callable[[str], None]
CompactFn = Callable[["list[Message]", int], int]


# ---------------------------------------------------------------------------
# Token budget + stopgap compaction (Phase 5 will replace hard_truncate)
# ---------------------------------------------------------------------------


def estimate_tokens(messages: list[Message]) -> int:
    """Rough token count over a message list (``len // 4``, per context.md §4.5)."""
    return sum(len(m.content) // 4 for m in messages)


def budget_for(num_ctx: int, reserve: int = REPLY_RESERVE_TOKENS) -> int:
    """Usable history budget = context window minus the reply reserve."""
    return max(1, num_ctx - reserve)


def hard_truncate(messages: list[Message], budget: int, *, keep_last: int = 4) -> int:
    """Drop oldest whole messages until the history fits ``budget``; returns how many were removed.

    TODO(TASKS 5.4): this is a lossy stopgap for the not-yet-built Phase 5 compaction. Replace it
    with a summarizing pass that folds the oldest turns into one assistant summary rather than
    discarding them. ``keep_last`` protects the most recent turns (the live task + recent results).
    """
    removed = 0
    while len(messages) > keep_last and estimate_tokens(messages) > budget:
        del messages[0]
        removed += 1
    return removed


# ---------------------------------------------------------------------------
# Target matching (pure; unit-tested)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ModelMatch:
    """Result of resolving a user-typed target against the installed list."""

    model: str | None
    error: str = ""


def match_model(target: str, installed: list[str]) -> ModelMatch:
    """Resolve ``target`` to exactly one installed model, else an error (prefix, then substring)."""
    target = target.strip()
    if target in installed:  # exact wins even if it's also a prefix of a longer tag
        return ModelMatch(target)
    prefix = sorted(n for n in installed if n.startswith(target))
    if len(prefix) == 1:
        return ModelMatch(prefix[0])
    if len(prefix) > 1:
        return ModelMatch(None, f"'{target}' is ambiguous — matches: {', '.join(prefix)}")
    sub = sorted(n for n in installed if target in n)
    if len(sub) == 1:
        return ModelMatch(sub[0])
    if len(sub) > 1:
        return ModelMatch(None, f"'{target}' is ambiguous — matches: {', '.join(sub)}")
    listing = ", ".join(sorted(installed)) or "(none)"
    return ModelMatch(None, f"no installed model matches '{target}'. Installed: {listing}")


# ---------------------------------------------------------------------------
# Switch result
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SwitchResult:
    ok: bool
    model: str
    num_ctx: int
    config: Config
    load_seconds: float = 0.0
    unloaded: str = ""
    context_pct: float = 0.0
    removed_messages: int = 0
    degraded: bool = False  # admin API unavailable -> name-only switch
    warnings: list[str] = field(default_factory=list)
    error: str = ""


# ---------------------------------------------------------------------------
# ModelManager
# ---------------------------------------------------------------------------


class ModelManager:
    """Owns the switch lifecycle; holds the base config and the native admin client."""

    def __init__(
        self,
        base_config: Config,
        admin: OllamaAdmin,
        *,
        executor: Executor | None = None,
        compact: CompactFn | None = None,
    ) -> None:
        # The base config keeps the original defaults + [models.*] table; effective configs are
        # derived from it per switch, never by mutating it (so resolution stays stable).
        self._base = base_config
        self.admin = admin
        self.executor = executor
        self._compact: CompactFn = compact or hard_truncate
        self.busy = False  # set by the REPL around a turn; blocks mid-turn switches

    # --- config resolution ------------------------------------------------
    def effective_config(self, model: str) -> Config:
        """Base config with ``model`` active and its per-model num_ctx/temperature applied."""
        return self._base.for_model(model)

    # --- admin queries (degrade to None/empty when the API is unavailable) -
    async def installed(self) -> list[InstalledModel] | None:
        try:
            return await self.admin.list_installed()
        except AdminUnavailable:
            return None

    async def installed_names(self) -> list[str] | None:
        models = await self.installed()
        return None if models is None else [m.name for m in models]

    async def loaded_names(self) -> set[str]:
        try:
            return {m.name for m in await self.admin.list_loaded()}
        except AdminUnavailable:
            return set()

    # --- the switch -------------------------------------------------------
    async def switch(
        self,
        state: SessionState,
        target: str,
        *,
        installed_names: list[str] | None | object = _UNSET,
        on_progress: ProgressFn | None = None,
    ) -> SwitchResult:
        """Run the full switch lifecycle to ``target``; see the module docstring for the sequence.

        ``installed_names`` may be pre-fetched by the caller (the REPL already lists them to match
        the target): a list means "admin available with these"; ``None`` means "admin unavailable";
        the default sentinel means "fetch it here".
        """
        progress = on_progress or (lambda _s: None)
        effective = self._base.for_model(target)

        # REPL-idle only: never swap VRAM out from under a running agent turn.
        if self.busy:
            return SwitchResult(
                False, target, effective.num_ctx, effective,
                error="cannot switch models mid-task; wait for the current turn to finish",
            )

        if installed_names is _UNSET:
            installed_names = await self.installed_names()
        degraded = installed_names is None
        warnings: list[str] = []

        if not degraded and target not in installed_names:  # type: ignore[operator]
            return SwitchResult(
                False, target, effective.num_ctx, effective,
                error=f"model '{target}' is not installed. Pull it with: ollama pull {target}",
            )

        current = state.model
        unloaded = ""
        # Exclusive residency: evict the current model *before* loading the target. Best-effort —
        # Ollama also evicts on demand, so a failure here must never abort the switch.
        if not degraded and current and current != target:
            progress(f"unloading {current}...")
            try:
                await self.admin.unload(current)
                unloaded = current
            except AdminUnavailable:
                warnings.append(f"could not unload {current} (Ollama will evict it on demand)")
            except Exception as exc:  # noqa: BLE001 - best-effort; keep going
                warnings.append(f"unload of {current} failed ({exc}); continuing")

        load_seconds = 0.0
        if not degraded:
            progress(f"loading {target}...")
            try:
                load_seconds = await self.admin.warmup(target, num_ctx=effective.num_ctx)
            except AdminUnavailable:
                warnings.append(f"could not preload {target}; it will load on first use")
            except Exception as exc:  # noqa: BLE001 - best-effort; keep going
                warnings.append(f"warmup of {target} failed ({exc}); it will load on first use")

        # Record the switch on the session so resumes/transcripts are honest (context.md §4.1).
        state.model = target
        state.append("system", f"[model switched to {target}]")
        if self.executor is not None:
            self.executor.active_model = target  # tag subsequent edit telemetry with this model

        # Recompute the budget for the (possibly smaller) window and compact if we no longer fit.
        budget = budget_for(effective.num_ctx)
        removed = 0
        if estimate_tokens(state.messages) > budget:
            removed = self._compact(state.messages, budget)
            if removed:
                warnings.append(
                    f"history exceeded the new {effective.num_ctx}-token window; dropped the "
                    f"{removed} oldest message(s) [stopgap - TASKS 5.4 will summarize instead]"
                )

        pct = 100.0 * estimate_tokens(state.messages) / effective.num_ctx
        if degraded:
            progress(f"switched to {target} (admin API unavailable - name-only switch)")
        else:
            progress(f"ready, {load_seconds:.1f}s")

        return SwitchResult(
            True, target, effective.num_ctx, effective,
            load_seconds=load_seconds, unloaded=unloaded, context_pct=pct,
            removed_messages=removed, degraded=degraded, warnings=warnings,
        )
