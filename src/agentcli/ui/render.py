"""Terminal rendering with rich: live token streaming then a final markdown re-render.

Shows a spinner until the first token, streams raw text live for responsiveness, then
replaces it with syntax-highlighted markdown (context.md §3, TASKS 1.4).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.spinner import Spinner
from rich.text import Text

from agentcli.providers.base import ModelProvider, WireMessage


def banner(console: Console, *, model: str, num_ctx: int, project_dir: str) -> None:
    console.print(f"[bold]agentcli[/bold]  model=[cyan]{model}[/cyan]  num_ctx={num_ctx}")
    console.print(f"[dim]project: {project_dir}[/dim]")
    console.print(
        "[dim]Enter for a newline - Alt+Enter to send - /help for commands - Ctrl+D to quit[/dim]"
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
