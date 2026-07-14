"""Interactive permission approval: a change preview plus a single-keystroke y / n / a answer.

The gate (permissions/gate.py) decides ALLOW / ASK / DENY from parsed action fields alone; when
the verdict is ASK, the loop computes a preview (a unified diff for writes/edits, "" for shell)
and calls one of these approvers to get the user's answer. ``a`` (always) is offered only when the
verdict carries a scope to remember — a bash command token or a file path
(context.md §4.6, TASKS 4.1 / 4.5).
"""

from __future__ import annotations

import sys

from rich.console import Console
from rich.text import Text

from arescode.permissions.gate import Approval, Approver, Verdict
from arescode.tools.registry import Action, action_summary

PREVIEW_LINES = 12


def auto_approver(console: Console) -> Approver:
    """--yolo mode: approve every ASK verdict (hard denials still bite before we get here)."""

    def approve(action: Action, verdict: Verdict, preview: str) -> Approval:
        return Approval(approved=True)

    return approve


def interactive_approver(console: Console) -> Approver:
    """Prompt the user with the change preview and read a single keystroke (y / n / a)."""

    def approve(action: Action, verdict: Verdict, preview: str) -> Approval:
        _render_request(console, action, verdict, preview)
        key = _read_key()
        console.print()  # end the prompt line after the keystroke
        if key == "a" and verdict.scope:
            return Approval(approved=True, remember=True)
        if key in ("y", "a"):  # 'a' with nothing to remember still approves this once
            return Approval(approved=True)
        return Approval(approved=False)

    return approve


def _render_request(console: Console, action: Action, verdict: Verdict, preview: str) -> None:
    console.print(
        f"[yellow]permission needed[/yellow] [bold]{action.tool}[/bold] "
        f"[dim]{action_summary(action)}[/dim]"
    )
    if preview:
        _render_diff(console, preview)
    always = ""
    if verdict.scope == "command" and verdict.key:
        always = f" / [a]lways allow '{verdict.key}'"
    elif verdict.scope == "file" and verdict.key:
        always = f" / [a]lways for {verdict.key}"
    console.print(f"  [y]es / [n]o{always} > ", end="")


def _render_diff(console: Console, diff: str) -> None:
    lines = diff.splitlines()
    for line in lines[:PREVIEW_LINES]:
        if line.startswith(("+++", "---")):
            style = "dim"
        elif line.startswith("+"):
            style = "green"
        elif line.startswith("-"):
            style = "red"
        elif line.startswith("@@"):
            style = "cyan"
        else:
            style = "dim"
        console.print(Text("  " + line, style=style))
    hidden = len(lines) - PREVIEW_LINES
    if hidden > 0:
        console.print(Text(f"  ... +{hidden} more diff line(s)", style="dim"))


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
