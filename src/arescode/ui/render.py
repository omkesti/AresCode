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
            f"  {mark} [dim]{escape(result.summary)} · {size}L · {duration * 1000:.0f}ms[/dim]"
        )
        if self.verbose and result.output:
            self.console.print(Text(result.output, style="dim"))

    def final(self, text: str) -> None:
        if text.strip():
            self.console.print(Markdown(text))

    def notice(self, text: str) -> None:
        note(self.console, text)

    def error(self, text: str) -> None:
        error(self.console, text)
