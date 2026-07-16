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

Compaction note: the *loop's* summarizing compaction lives in :mod:`arescode.core.context`
(TASKS 5.4). This switch path deliberately uses the faster ``hard_truncate`` from that module
instead — a shrinking-window swap happens with the old model already evicted and the new one just
warmed, so making a slow summarization call mid-swap would be the wrong tradeoff. Dropping the
oldest whole messages with a visible warning keeps the switch snappy; the ``compact`` seam remains
if a caller ever wants to inject summarization here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from arescode.core.context import budget_for, estimate_tokens, hard_truncate
from arescode.providers.ollama_admin import (
    AdminUnavailable,
    InstalledModel,
    ModelLoadError,
    OllamaAdmin,
)

if TYPE_CHECKING:
    from arescode.config import Config
    from arescode.core.state import Message, SessionState
    from arescode.tools.registry import Executor

# Token-budget primitives live in core/context.py (their canonical home, TASKS 5.3); re-exported
# here so the switch path and its tests keep importing them from ``arescode.core.models``.
__all__ = ["ModelManager", "ModelMatch", "SwitchResult", "budget_for", "estimate_tokens",
           "hard_truncate", "match_model"]

# Sentinel: "fetch the installed list yourself". Distinct from None ("admin unavailable").
_UNSET: object = object()

ProgressFn = Callable[[str], None]
CompactFn = Callable[["list[Message]", int], int]


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

    # --- startup verification --------------------------------------------
    async def verify_loads(self, model: str) -> float:
        """Force ``model`` resident to confirm it actually loads; return wall-clock load seconds.

        Raises :class:`ModelLoadError` if the model can't load (e.g. too large for VRAM) and
        :class:`AdminUnavailable` if the native API can't be reached (can't verify → caller trusts
        it and loads lazily). Used at startup to self-heal a poisoned remembered default (D13).
        """
        effective = self._base.for_model(model)
        return await self.admin.warmup(model, num_ctx=effective.num_ctx)

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
            except ModelLoadError as exc:
                # The model actively failed to load (e.g. too large for VRAM). Abort the switch and
                # stay on the current model — crucially, ok=False keeps the REPL from persisting a
                # model that can't run (D13). The current model was evicted above, but Ollama
                # reloads it on demand, so `state.model` is still valid and untouched.
                return SwitchResult(
                    False, target, effective.num_ctx, effective,
                    error=(
                        f"{target} failed to load; staying on {current or 'the current model'}. "
                        f"This usually means it needs more VRAM than is free right now. "
                        f"Details: {exc}"
                    ),
                )
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
                    f"{removed} oldest message(s) to fit"
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
