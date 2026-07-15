"""Interactive model picker for ``/model`` with no argument (D12).

Lists the installed models (from the native admin API), marking the active one and any resident in
VRAM, and reads a single choice — a number or a name/prefix. Esc, Ctrl-C, or an empty line cancels.
When the admin API is unavailable the REPL falls back to :func:`free_text_model` instead.

The selection maths lives in :func:`resolve_choice` (pure, unit-tested); the prompt_toolkit read is
kept thin so it can't hide logic.
"""

from __future__ import annotations

from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings
from rich.console import Console

from arescode.providers.ollama_admin import InstalledModel
from arescode.ui import render


def resolve_choice(raw: str, installed_names: list[str]) -> str | None:
    """Map a picker answer to a model name: a 1-based index, or a name/prefix passed through.

    Returns ``None`` for an empty answer or an out-of-range number. A non-numeric answer is returned
    verbatim for the caller to resolve with :func:`~arescode.core.models.match_model`.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        idx = int(raw)
        if 1 <= idx <= len(installed_names):
            return installed_names[idx - 1]
        return None
    return raw


def _cancel_bindings() -> KeyBindings:
    """Esc cancels the picker (returns None from prompt_async)."""
    kb = KeyBindings()

    @kb.add("escape", eager=True)
    def _(event) -> None:  # noqa: ANN001
        event.app.exit(result=None)

    return kb


async def pick_model(
    console: Console,
    installed: list[InstalledModel],
    *,
    active: str,
    loaded: set[str],
) -> str | None:
    """Render the installed list and read one choice; returns a model name or None (cancelled)."""
    if not installed:
        render.note(console, "no installed models reported by the admin API")
        return None

    console.print("[bold]Installed models[/bold]  [dim](number or name; Esc to cancel)[/dim]")
    for i, m in enumerate(installed, 1):
        marks = []
        if m.name == active:
            marks.append("active")
        if m.name in loaded:
            marks.append("in VRAM")
        badge = f"  [green]({', '.join(marks)})[/green]" if marks else ""
        size = f"{m.size_gb:.1f}GB" if m.size else "?"
        quant = f" {m.quantization}" if m.quantization else ""
        console.print(f"  [cyan]{i:>2}[/cyan]. {m.name}  [dim]{size}{quant}[/dim]{badge}")

    raw = await _read_line(console, "select> ", bindings=_cancel_bindings())
    if raw is None:
        return None
    return resolve_choice(raw, [m.name for m in installed])


async def free_text_model(console: Console) -> str | None:
    """Fallback when the admin API is unavailable: just ask for a model name."""
    raw = await _read_line(console, "model name> ")
    return raw.strip() if raw and raw.strip() else None


async def _read_line(
    console: Console, prompt: str, *, bindings: KeyBindings | None = None
) -> str | None:
    session: PromptSession = PromptSession()
    try:
        return await session.prompt_async(prompt, key_bindings=bindings)
    except (EOFError, KeyboardInterrupt):
        return None
