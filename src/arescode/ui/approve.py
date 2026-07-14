"""Interactive permission approval: a preview plus a single-keystroke y / n / a answer.

The gate (permissions/gate.py) decides ALLOW / ASK / DENY from parsed action fields alone; when
the verdict is ASK, the loop calls one of these approvers to get the user's answer. ``a`` (always)
is offered only when the verdict carries a scope to remember — a bash command token or a file path
(context.md §4.6, TASKS 4.1 / 4.5).
"""

from __future__ import annotations

import sys

from rich.console import Console
from rich.text import Text

from arescode.permissions.gate import Approval, Approver, Verdict
from arescode.tools.registry import Action, EditFileAction, WriteFileAction, action_summary

PREVIEW_LINES = 12


def auto_approver(console: Console) -> Approver:
    """--yolo mode: approve every ASK verdict (hard denials still bite before we get here)."""

    def approve(action: Action, verdict: Verdict) -> Approval:
        return Approval(approved=True)

    return approve


def interactive_approver(console: Console) -> Approver:
    """Prompt the user with a preview and read a single keystroke (y / n / a)."""

    def approve(action: Action, verdict: Verdict) -> Approval:
        _render_request(console, action, verdict)
        key = _read_key()
        console.print()  # end the prompt line after the keystroke
        if key == "a" and verdict.scope:
            return Approval(approved=True, remember=True)
        if key in ("y", "a"):  # 'a' with nothing to remember still approves this once
            return Approval(approved=True)
        return Approval(approved=False)

    return approve


def _render_request(console: Console, action: Action, verdict: Verdict) -> None:
    console.print(
        f"[yellow]permission needed[/yellow] [bold]{action.tool}[/bold] "
        f"[dim]{action_summary(action)}[/dim]"
    )
    _render_preview(console, action)
    always = ""
    if verdict.scope == "command" and verdict.key:
        always = f" / [a]lways allow '{verdict.key}'"
    elif verdict.scope == "file" and verdict.key:
        always = f" / [a]lways for {verdict.key}"
    console.print(f"  [y]es / [n]o{always} > ", end="")


def _render_preview(console: Console, action: Action) -> None:
    """Preview what a write/edit will change; bash needs no preview beyond its command line."""
    if isinstance(action, WriteFileAction):
        _print_capped(console, action.content.splitlines(), prefix="+", style="green")
    elif isinstance(action, EditFileAction):
        shown = 0
        for sr in action.edits:
            for line in sr.search.splitlines():
                console.print(Text(f"  - {line}", style="red"))
                shown += 1
            for line in sr.replace.splitlines():
                console.print(Text(f"  + {line}", style="green"))
                shown += 1
            if shown >= PREVIEW_LINES:
                console.print(Text("  ...", style="dim"))
                break


def _print_capped(console: Console, lines: list[str], *, prefix: str, style: str) -> None:
    for line in lines[:PREVIEW_LINES]:
        console.print(Text(f"  {prefix} {line}", style=style))
    hidden = len(lines) - PREVIEW_LINES
    if hidden > 0:
        console.print(Text(f"  ... +{hidden} more line(s)", style="dim"))


def _read_key() -> str:
    """Read one keypress and return it lowercased. Ctrl-C/Ctrl-D/Esc read as 'n' (decline)."""
    if not sys.stdin.isatty():  # piped stdin (never in the real REPL): fall back to a line read
        try:
            return (input().strip()[:1] or "n").lower()
        except EOFError:
            return "n"
    try:
        import msvcrt  # Windows: a true single-keystroke read
    except ImportError:
        import termios
        import tty

        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
    else:
        ch = msvcrt.getwch()
    if ch in ("\x03", "\x04", "\x1b"):  # Ctrl-C / Ctrl-D / Esc
        return "n"
    return ch.lower()
