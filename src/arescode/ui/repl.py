"""Interactive REPL: prompt_toolkit input, slash commands, and the agent loop.

Enter sends; Ctrl+J (or Alt+Enter where the terminal allows it) inserts a newline. Each message
runs the agent loop (the model can read files, search, and run commands), rendered as a tool
trace. Ctrl+C cancels the current turn without killing the session; Ctrl+D exits
(context.md §3, TASKS 1.3 / 1.6 / 2.8).
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
from arescode.core.context import load_system_prompt
from arescode.core.loop import run_turn
from arescode.core.state import SessionState
from arescode.permissions.gate import Gate
from arescode.providers.openai_compat import OpenAICompatProvider
from arescode.tools.registry import Executor
from arescode.ui import render
from arescode.ui.approve import auto_approver, interactive_approver

HELP_TEXT = """\
Commands:
  /help            show this help
  /clear           reset the conversation history
  /model <name>    switch the active model (no arg shows the current one)
  /verbose         toggle full tool output in the trace
  /stats           show edit telemetry for this session
  /allow [cmd]     no arg: show the allowlist; with a token: always allow that bash command
  /deny <cmd>      remove a bash command from the session allowlist
  /exit, /quit     leave AresCode
Input:
  Enter            send the message
  Ctrl+J           insert a newline (also Alt+Enter where the terminal allows it)
  Ctrl+C           cancel the current turn
  Ctrl+D           exit
"""


@dataclass(slots=True)
class Command:
    """The outcome of handling a /slash command."""

    action: Literal["continue", "exit"]
    model: str | None = None  # set when /model switches the active model
    toggle_verbose: bool = False  # set when /verbose is issued
    show_stats: bool = False  # set when /stats is issued
    show_allow: bool = False  # set when /allow is issued with no argument
    allow: str | None = None  # bash token to add to the session allowlist (/allow <cmd>)
    deny: str | None = None  # bash token to drop from the session allowlist (/deny <cmd>)


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
    if name == "/verbose":
        return Command(action="continue", toggle_verbose=True)
    if name == "/stats":
        return Command(action="continue", show_stats=True)
    if name == "/allow":
        if not arg:
            return Command(action="continue", show_allow=True)
        return Command(action="continue", allow=arg.split()[0])
    if name == "/deny":
        if not arg:
            render.note(console, "usage: /deny <command>")
            return Command(action="continue")
        return Command(action="continue", deny=arg.split()[0])
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


async def run(
    *, config: Config, project_dir: Path, resume: bool = False, yolo: bool = False
) -> None:
    """Run the interactive agent loop until the user exits."""
    console = Console()
    provider = OpenAICompatProvider.from_config(config)
    gate = Gate.from_config(project_dir, config)
    approver = auto_approver(console) if yolo else interactive_approver(console)
    executor = Executor(project_dir, config, gate=gate, approver=approver)
    observer = render.ConsoleObserver(console, verbose=False)
    system_prompt = load_system_prompt()
    state = _load_session(project_dir, config, console, resume=resume)

    render.banner(console, model=state.model, num_ctx=config.num_ctx, project_dir=str(project_dir))
    if yolo:
        console.print(
            "[bold red]--yolo: every action auto-approved. "
            "Hard-denied commands and path escapes are still blocked.[/bold red]"
        )

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
            if command.toggle_verbose:
                observer.verbose = not observer.verbose
                render.note(console, f"verbose {'on' if observer.verbose else 'off'}")
            if command.show_stats:
                render.note(console, executor.stats.summary())
            if command.show_allow:
                render.note(console, gate.describe_allowlist())
            if command.allow:
                gate.allow_command(command.allow)
                render.note(console, f"always allowing bash command: {command.allow}")
            if command.deny:
                removed = gate.deny_command(command.deny)
                render.note(
                    console,
                    f"removed {command.deny} from the allowlist" if removed
                    else f"{command.deny} was not in the session allowlist",
                )
            continue

        task = asyncio.ensure_future(
            run_turn(
                user_input,
                state=state,
                provider=provider,
                executor=executor,
                system_prompt=system_prompt,
                observer=observer,
                max_steps=config.max_steps,
            )
        )
        try:
            await task
        except KeyboardInterrupt:  # Ctrl+C mid-turn: cancel, keep the session
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, KeyboardInterrupt):
                pass
            render.note(console, "cancelled")

        state.save(project_dir)

    if executor.stats.attempts:
        render.note(console, executor.stats.summary())
    render.note(console, "bye")
