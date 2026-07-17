"""Terminal rendering with rich: chat streaming, the final answer, and the tool-trace UI.

``stream_response`` (Phase 1) streams plain chat. ``ConsoleObserver`` (Phase 2) renders the
agent loop: a spinner while the model thinks, a compact colored line per tool call (tool, args,
duration, result size), and the final answer as markdown. ``/verbose`` shows full tool output
(context.md §3, TASKS 1.4 / 2.8).
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from typing import Any

from rich import box
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text

from arescode.providers.base import ModelProvider, WireMessage
from arescode.tools.registry import Action, ToolResult, action_summary
from arescode.ui import theme

# Lines of tool output shown inline when not in /verbose mode. Enough to see a short file
# list or the head of a read, without flooding the trace; /verbose shows everything.
PREVIEW_LINES = 12

# The welcome screen's numbered notes: (heading, detail) — same shape as Claude Code's
# "Security notes" block. Keep it to three items so the banner stays one screen tall.
_NOTES: tuple[tuple[str, str], ...] = (
    (
        "AresCode runs fully local (Ollama)",
        "Responses come from a small local model; expect rough edges.",
    ),
    (
        "AresCode can make mistakes",
        "Always review edits, diffs, and commands before approving them.",
    ),
    (
        "Enter sends - Ctrl+J newline - Ctrl+C cancel - Ctrl+D quit",
        "Run /help at any time to list all slash commands.",
    ),
)


def banner(console: Console, *, model: str, num_ctx: int, project_dir: str) -> None:
    """The Claude Code-style welcome screen: boxed greeting, wordmark, session info, notes."""
    console.print()
    greeting = Text.from_markup("Welcome to [bold]AresCode[/bold]!")
    console.print(Panel(greeting, box=box.ROUNDED, border_style=theme.PRIMARY,
                        expand=False, padding=(0, 1)))
    console.print()
    for line in theme.logo_lines():
        console.print(line)
    console.print()
    console.print(
        f"  model: [{theme.PRIMARY_LIGHT}]{model}[/]  [dim]num_ctx={num_ctx}[/dim]"
    )
    console.print(f"  [dim]project: {project_dir}[/dim]")
    console.print()
    console.print("  [bold]Notes:[/bold]")
    console.print()
    for i, (heading, detail) in enumerate(_NOTES, 1):
        console.print(f"  [bold]{i}. {heading}[/bold]")
        console.print(f"     [dim]{detail}[/dim]")
    console.print()
    console.print(f"  [{theme.PRIMARY_LIGHT}]Type a request and press Enter to begin...[/]")


def note(console: Console, message: str) -> None:
    console.print(f"[dim]{message}[/dim]")


def error(console: Console, message: str) -> None:
    console.print(f"[red]error:[/red] {message}")


async def stream_response(
    provider: ModelProvider,
    messages: Sequence[WireMessage],
    *,
    console: Console,
    **opts: Any,
) -> str:
    """Stream a response live, then render it as markdown. Returns the full text."""
    parts: list[str] = []
    spinner = Spinner("dots", text=Text(" thinking...", style="dim"))

    with Live(spinner, console=console, refresh_per_second=15, transient=True) as live:
        async for chunk in provider.chat(messages, **opts):
            if not chunk.content:
                continue
            parts.append(chunk.content)
            live.update(Text("".join(parts)))

    text = "".join(parts)
    if text.strip():
        console.print(Markdown(text))
    return text


class ConsoleObserver:
    """LoopObserver implementation that renders the agent loop to a rich console."""

    def __init__(self, console: Console, *, verbose: bool = False) -> None:
        self.console = console
        self.verbose = verbose

    def thinking(self) -> AbstractContextManager:
        return self.console.status("[dim]thinking...[/dim]", spinner="dots")

    def assistant_text(self, text: str) -> None:
        # The model's between-step reasoning; shown dimmed so tool traces stand out.
        self.console.print(Text(text.strip(), style="dim italic"))

    def tool_start(self, action: Action) -> None:
        self.console.print(
            f"[{theme.PRIMARY}]●[/] [bold]{action.tool}[/bold] "
            f"[dim]{escape(action_summary(action))}[/dim]"
        )

    def tool_end(self, action: Action, result: ToolResult, duration: float) -> None:
        mark = "[green]ok[/green]" if result.ok else "[red]![/red]"
        size = len(result.output.splitlines())
        self.console.print(
            f"  {mark} [dim]{escape(result.summary)} | {size}L | {duration * 1000:.0f}ms[/dim]"
        )
        if result.diff:  # edit/write: show the change (green/red), TASKS 3.5
            self._render_diff(result.diff)
        elif self.verbose and result.output:
            self.console.print(Text(result.output, style="dim"))
        elif result.output:  # non-verbose: a short preview so the result is visible on screen
            self._render_preview(result.output)

    def _render_preview(self, output: str) -> None:
        """Show the first few lines of a tool result so the user sees what the model saw.

        Without this, a read-only tool prints only its one-line summary and the actual result
        (the file list, the matches) reaches the model but never the screen — so the model's
        prose ("I listed the files") is the only trace of data the user never saw. Full output
        stays behind /verbose.
        """
        lines = output.splitlines()
        for line in lines[:PREVIEW_LINES]:
            self.console.print(Text("  " + line, style="dim"))
        hidden = len(lines) - PREVIEW_LINES
        if hidden > 0:
            self.console.print(
                Text(f"  ... +{hidden} more line(s) - /verbose for all", style="dim")
            )

    def _render_diff(self, diff: str) -> None:
        for line in diff.splitlines():
            if line.startswith(("+++", "---")):
                style = "dim"
            elif line.startswith("+"):
                style = "green"
            elif line.startswith("-"):
                style = "red"
            elif line.startswith("@@"):
                style = theme.PRIMARY_LIGHT
            else:
                style = "dim"
            self.console.print(Text(line, style=style))

    def final(self, text: str) -> None:
        if text.strip():
            self.console.print(Markdown(text))

    def notice(self, text: str) -> None:
        note(self.console, text)

    def error(self, text: str) -> None:
        error(self.console, text)
