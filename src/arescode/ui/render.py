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

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.markup import escape
from rich.spinner import Spinner
from rich.text import Text

from arescode.providers.base import ModelProvider, WireMessage
from arescode.tools.registry import Action, ToolResult, action_summary

# Lines of tool output shown inline when not in /verbose mode. Enough to see a short file
# list or the head of a read, without flooding the trace; /verbose shows everything.
PREVIEW_LINES = 12


def banner(console: Console, *, model: str, num_ctx: int, project_dir: str) -> None:
    console.print(f"[bold]AresCode[/bold]  model=[cyan]{model}[/cyan]  num_ctx={num_ctx}")
    console.print(f"[dim]project: {project_dir}[/dim]")
    console.print(
        "[dim]Enter to send - Ctrl+J for a newline - /help for commands - Ctrl+D to quit[/dim]"
    )


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
            f"[cyan]>[/cyan] [bold]{action.tool}[/bold] [dim]{escape(action_summary(action))}[/dim]"
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
                style = "cyan"
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
