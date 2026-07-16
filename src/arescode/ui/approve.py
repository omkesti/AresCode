"""Interactive permission approval: a change preview plus an arrow-key menu (y / n / a).

The gate (permissions/gate.py) decides ALLOW / ASK / DENY from parsed action fields alone; when
the verdict is ASK, the loop computes a preview (a unified diff for writes/edits, "" for shell)
and calls one of these approvers to get the user's answer. The interactive approver renders a small
menu the user navigates with up/down + Enter (Esc cancels); single-key y/n/a and number keys still
work as shortcuts. "Always" is offered only when the verdict carries a scope to remember — a bash
command token or a file path (context.md §4.6, TASKS 4.1 / 4.5).
"""

from __future__ import annotations

import sys
from contextlib import contextmanager

from rich.console import Console, Group
from rich.markup import escape
from rich.text import Text

from arescode.permissions.gate import Approval, Approver, Verdict
from arescode.tools.registry import Action, action_summary

PREVIEW_LINES = 12
_MENU_HINT = "up/down to move, Enter to select, Esc to cancel"


def auto_approver(console: Console) -> Approver:
    """--yolo mode: approve every ASK verdict (hard denials still bite before we get here)."""

    def approve(action: Action, verdict: Verdict, preview: str) -> Approval:
        return Approval(approved=True)

    return approve


def interactive_approver(console: Console) -> Approver:
    """Prompt with the change preview and an arrow-key menu; return the user's Approval."""

    def approve(action: Action, verdict: Verdict, preview: str) -> Approval:
        _render_request(console, action, verdict, preview)
        options = _build_options(action, verdict)
        idx = _menu_select(console, options)
        if idx is None:
            console.print("  [dim]declined[/dim]")
            return Approval(approved=False)
        label, approval = options[idx]
        console.print(f"  [dim]-> {label}[/dim]")
        return approval

    return approve


def _build_options(action: Action, verdict: Verdict) -> list[tuple[str, Approval]]:
    """Menu choices for an ASK verdict: yes / no, plus 'always' when there's a scope to remember."""
    tool = getattr(action, "tool", "")
    yes = "Yes, run it" if tool == "bash" else "Yes, apply the change"
    options: list[tuple[str, Approval]] = [
        (yes, Approval(approved=True)),
        ("No, skip it", Approval(approved=False)),
    ]
    if verdict.scope == "command" and verdict.key:
        options.append(
            (f"Always allow '{verdict.key}' this session", Approval(approved=True, remember=True))
        )
    elif verdict.scope == "file" and verdict.key:
        options.append(
            (f"Always allow edits to {verdict.key} this session",
             Approval(approved=True, remember=True))
        )
    return options


def _render_request(console: Console, action: Action, verdict: Verdict, preview: str) -> None:
    # escape() so a path/command containing [] is never mistaken for rich markup.
    console.print(
        f"[yellow]permission needed[/yellow] [bold]{escape(action.tool)}[/bold] "
        f"[dim]{escape(action_summary(action))}[/dim]"
    )
    if preview:
        _render_diff(console, preview)


# ---------------------------------------------------------------------------
# The arrow-key menu
# ---------------------------------------------------------------------------


def _resolve_menu_key(key: str, idx: int, n: int) -> tuple[int, str]:
    """Pure state transition for a keypress -> (new_index, 'move' | 'select' | 'cancel').

    Kept separate from the terminal read so it can be unit-tested without a TTY.
    """
    if key == "up":
        return (idx - 1) % n, "move"
    if key == "down":
        return (idx + 1) % n, "move"
    if key == "enter":
        return idx, "select"
    if key == "esc":
        return idx, "cancel"
    if key.isdigit():
        value = int(key)
        return (value - 1, "select") if 1 <= value <= n else (idx, "move")
    if key == "y":
        return 0, "select"
    if key == "n":
        return min(1, n - 1), "select"
    if key == "a" and n >= 3:
        return 2, "select"
    return idx, "move"


def _render_menu(options: list[tuple[str, Approval]], idx: int) -> Group:
    rows: list[Text] = []
    for i, (label, _) in enumerate(options):
        if i == idx:
            rows.append(Text(f"> {label}", style="bold cyan"))
        else:
            rows.append(Text(f"  {label}", style="dim"))
    rows.append(Text(f"  [{_MENU_HINT}]", style="dim"))
    return Group(*rows)


def _menu_select(console: Console, options: list[tuple[str, Approval]]) -> int | None:
    """Show the menu and return the chosen index, or None if cancelled."""
    n = len(options)
    if not sys.stdin.isatty() or not console.is_terminal:
        return _menu_select_line(options)  # piped stdin / non-terminal (tests): read one line

    # Imported lazily so this module stays importable where rich.live isn't wanted.
    from rich.live import Live

    idx = 0
    with _raw_mode(), Live(
        _render_menu(options, idx), console=console, auto_refresh=False, transient=True
    ) as live:
        live.refresh()
        while True:
            new_idx, act = _resolve_menu_key(_read_menu_key(), idx, n)
            if act == "select":
                return new_idx
            if act == "cancel":
                return None
            idx = new_idx
            live.update(_render_menu(options, idx))
            live.refresh()


def _menu_select_line(options: list[tuple[str, Approval]]) -> int | None:
    """Non-interactive fallback: read a single line and map it to a choice (or None to decline)."""
    try:
        raw = input().strip().lower()
    except EOFError:
        return None
    if not raw:
        return None
    idx, act = _resolve_menu_key(_line_to_key(raw), 0, len(options))
    return idx if act == "select" else None


def _line_to_key(raw: str) -> str:
    first = raw[0]
    if first.isdigit():
        return first
    return first  # 'y' / 'n' / 'a' shortcuts resolve the same way as in the menu


# ---------------------------------------------------------------------------
# Raw single-keystroke reading (arrow keys included), cross-platform
# ---------------------------------------------------------------------------


def _read_menu_key() -> str:
    """Block for one keypress; return 'up' | 'down' | 'enter' | 'esc' | a lowercased char."""
    try:
        import msvcrt  # Windows: getwch returns arrows as a two-call \x00/\xe0 prefix + code
    except ImportError:
        return _read_menu_key_posix()
    ch = msvcrt.getwch()
    if ch in ("\x00", "\xe0"):
        return {"H": "up", "P": "down", "K": "left", "M": "right"}.get(msvcrt.getwch(), "")
    if ch in ("\r", "\n"):
        return "enter"
    if ch in ("\x1b", "\x03", "\x04"):  # Esc / Ctrl-C / Ctrl-D all cancel
        return "esc"
    return ch.lower()


def _read_menu_key_posix() -> str:
    ch = sys.stdin.read(1)
    if ch == "\x1b":  # arrow escape sequence (\x1b[A) or a lone Esc
        if _posix_input_ready() and sys.stdin.read(1) == "[":
            return {"A": "up", "B": "down", "C": "right", "D": "left"}.get(sys.stdin.read(1), "")
        return "esc"
    if ch in ("\r", "\n"):
        return "enter"
    if ch in ("\x03", "\x04"):
        return "esc"
    return ch.lower()


def _posix_input_ready() -> bool:
    import select

    return bool(select.select([sys.stdin], [], [], 0.05)[0])


@contextmanager
def _raw_mode():
    """cbreak mode for the duration of the menu (Unix). No-op on Windows (msvcrt reads raw already).

    cbreak, not raw: it disables line buffering + echo but keeps output post-processing, so rich's
    multi-line Live redraw (which relies on newline->CRLF) still positions the cursor correctly.
    """
    try:
        import termios
        import tty
    except ImportError:  # Windows
        yield
        return
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


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
