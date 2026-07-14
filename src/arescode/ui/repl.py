"""Interactive REPL: prompt_toolkit input, slash commands, streaming responses.

Enter sends; Ctrl+J (or Alt+Enter where the terminal allows it) inserts a newline.
Persistent history, Ctrl+C cancels the current generation without killing the session,
Ctrl+D exits (context.md §3, TASKS 1.3 / 1.6).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from rich.console import Console

from arescode.config import Config
from arescode.core.state import SessionState
from arescode.providers.base import ProviderError
from arescode.providers.openai_compat import OpenAICompatProvider
from arescode.ui import render

HELP_TEXT = """\
Commands:
  /help            show this help
  /clear           reset the conversation history
  /model <name>    switch the active model (no arg shows the current one)
  /exit, /quit     leave AresCode
Input:
  Enter            send the message
  Ctrl+J           insert a newline (also Alt+Enter where the terminal allows it)
  Ctrl+C           cancel the current response
  Ctrl+D           exit
"""


@dataclass(slots=True)
class Command:
    """The outcome of handling a /slash command."""

    action: Literal["continue", "exit"]
    model: str | None = None  # set when /model switches the active model


def parse_command(line: str, state: SessionState, console: Console) -> Command:
    """Handle a /slash command, mutating ``state`` where relevant."""
    parts = line.strip().split(maxsplit=1)
    name = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if name in ("/exit", "/quit"):
        return Command(action="exit")
    if name == "/help":
        console.print(HELP_TEXT)
        return Command(action="continue")
    if name == "/clear":
        state.clear()
        render.note(console, "history cleared")
        return Command(action="continue")
    if name == "/model":
        if not arg:
            render.note(console, f"current model: {state.model}")
            return Command(action="continue")
        state.model = arg
        render.note(console, f"model switched to {arg}")
        return Command(action="continue", model=arg)

    render.note(console, f"unknown command: {name} (try /help)")
    return Command(action="continue")


def _build_key_bindings() -> KeyBindings:
    """Enter sends; Ctrl+J and Alt+Enter insert a newline.

    prompt_toolkit's default multiline binding is the opposite (Enter=newline,
    Alt+Enter=submit), which is unintuitive for chat and unusable on Windows Terminal,
    where Alt+Enter is intercepted as the fullscreen toggle and never reaches the app.
    So we bind Enter to submit and give newlines dedicated keys.
    """
    kb = KeyBindings()

    @kb.add("enter")
    def _submit(event) -> None:
        event.current_buffer.validate_and_handle()

    @kb.add("c-j")  # Ctrl+J: reliable newline in every terminal
    @kb.add("escape", "enter")  # Alt+Enter: newline where the terminal delivers it
    def _newline(event) -> None:
        event.current_buffer.insert_text("\n")

    return kb


def _history_path() -> Path:
    directory = Path.home() / ".arescode"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "history"


def _load_session(
    project_dir: Path, config: Config, console: Console, *, resume: bool
) -> SessionState:
    if not resume:
        return SessionState.new(config.model)
    state = SessionState.load_latest(project_dir)
    if state is None:
        render.note(console, "no previous session found; starting fresh")
        return SessionState.new(config.model)
    render.note(console, f"resumed session {state.session_id} ({len(state.messages)} messages)")
    return state


async def run(*, config: Config, project_dir: Path, resume: bool = False) -> None:
    """Run the interactive chat loop until the user exits."""
    console = Console()
    provider = OpenAICompatProvider.from_config(config)
    state = _load_session(project_dir, config, console, resume=resume)

    render.banner(console, model=state.model, num_ctx=config.num_ctx, project_dir=str(project_dir))

    prompt_session: PromptSession = PromptSession(
        history=FileHistory(str(_history_path())),
        multiline=True,
        key_bindings=_build_key_bindings(),
    )

    while True:
        try:
            user_input = await prompt_session.prompt_async("\nyou> ")
        except EOFError:  # Ctrl+D
            break
        except KeyboardInterrupt:  # Ctrl+C at an empty prompt
            continue

        user_input = user_input.strip()
        if not user_input:
            continue

        if user_input.startswith("/"):
            command = parse_command(user_input, state, console)
            if command.action == "exit":
                break
            if command.model:
                config = config.model_copy(update={"model": command.model})
                provider = OpenAICompatProvider.from_config(config)
            continue

        state.user(user_input)
        task = asyncio.ensure_future(
            render.stream_response(provider, state.to_wire(), console=console)
        )
        try:
            reply = await task
        except KeyboardInterrupt:  # Ctrl+C mid-generation: cancel, keep the session
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, KeyboardInterrupt):
                pass
            render.note(console, "cancelled")
            continue
        except ProviderError as exc:
            render.error(console, str(exc))
            continue

        state.assistant(reply)
        state.save(project_dir)

    render.note(console, "bye")
