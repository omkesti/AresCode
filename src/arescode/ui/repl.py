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

from arescode.config import Config, save_last_model
from arescode.core.context import load_system_prompt
from arescode.core.loop import run_turn
from arescode.core.models import ModelManager, match_model
from arescode.core.state import SessionState
from arescode.permissions.gate import Gate
from arescode.providers.ollama_admin import OllamaAdmin
from arescode.providers.openai_compat import OpenAICompatProvider
from arescode.tools.registry import Executor
from arescode.ui import render
from arescode.ui.approve import auto_approver, interactive_approver
from arescode.ui.model_select import free_text_model, pick_model

HELP_TEXT = """\
Commands:
  /help            show this help
  /clear           reset the conversation history
  /model [name]    no arg: pick from installed models; with a name: switch (unloads the old one).
                   The chosen model is remembered as the default for the next launch.
  /verbose         toggle full tool output in the trace
  /stats           show edit telemetry for this session (grouped by model)
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
    model_pick: bool = False  # /model with no arg -> open the interactive picker
    model_target: str | None = None  # /model <name> -> switch target (resolved by the REPL)
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
        # The actual switch (validate -> unload -> warmup -> budget) is async and runs in the
        # REPL; parse_command only signals intent. No arg opens the picker; a name is a target.
        if not arg:
            return Command(action="continue", model_pick=True)
        return Command(action="continue", model_target=arg)

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


async def _activate_model(
    manager: ModelManager,
    state: SessionState,
    base_config: Config,
    console: Console,
    *,
    resume: bool,
) -> Config:
    """Resolve the config for the session's active model at startup (applies per-model settings).

    On ``--resume`` the recorded model is restored; if it is no longer installed we warn and fall
    back to the configured default (D12). Fresh sessions simply apply the default model's per-model
    settings so the very first provider already uses the right num_ctx.
    """
    active = state.model or base_config.model
    if resume:
        names = await manager.installed_names()
        if names is not None and active not in names:
            render.note(
                console,
                f"session model '{active}' is no longer installed; "
                f"falling back to {base_config.model}",
            )
            active = base_config.model
    state.model = active
    return manager.effective_config(active)


async def _switch_model(
    *,
    manager: ModelManager,
    state: SessionState,
    config: Config,
    provider: OpenAICompatProvider,
    console: Console,
    target: str | None,
) -> tuple[Config, OpenAICompatProvider]:
    """Handle a ``/model`` command: pick/resolve a target, run the switch, rebuild the provider.

    Returns the (possibly unchanged) config + provider so the caller can keep serving chat from the
    OpenAI-compat endpoint (D5) with the new model's settings.
    """
    installed = await manager.installed()
    names = None if installed is None else [m.name for m in installed]

    if target is None:  # /model with no arg -> picker (or free-text when admin is unavailable)
        if installed is None:
            render.note(console, "admin API unavailable — type a model name to switch")
            target = await free_text_model(console)
        else:
            target = await pick_model(
                console, installed, active=state.model, loaded=await manager.loaded_names()
            )
        if not target:
            render.note(console, "model switch cancelled")
            return config, provider

    if names is not None:  # resolve a prefix / number-derived name against the installed list
        matched = match_model(target, names)
        if matched.model is None:
            render.error(console, matched.error)
            return config, provider
        target = matched.model

    if target == state.model:
        render.note(console, f"already using {target}")
        return config, provider

    result = await manager.switch(
        state, target, installed_names=names, on_progress=lambda s: render.note(console, s)
    )
    if not result.ok:
        render.error(console, result.error)
        return config, provider

    for warning in result.warnings:
        render.note(console, warning)
    config = result.config
    provider = OpenAICompatProvider.from_config(config)
    # Remember this choice so the next launch starts on it (D13); overrides only the built-in
    # default, so a config-file `model` or `--model` flag still wins next time.
    save_last_model(result.model)
    console.print(
        f"[bold cyan]{result.model}[/bold cyan]  num_ctx={result.num_ctx}  "
        f"context {result.context_pct:.0f}% used"
    )
    return config, provider


async def run(
    *, config: Config, project_dir: Path, resume: bool = False, yolo: bool = False
) -> None:
    """Run the interactive agent loop until the user exits."""
    console = Console()
    gate = Gate.from_config(project_dir, config)
    approver = auto_approver(console) if yolo else interactive_approver(console)
    # The loop runs the interactive gate (allow/ask/approve); the executor shares the same gate as
    # a hard-deny backstop so a blocklisted command or path escape can never slip through.
    executor = Executor(project_dir, config, gate=gate)
    observer = render.ConsoleObserver(console, verbose=False)
    system_prompt = load_system_prompt()
    state = _load_session(project_dir, config, console, resume=resume)

    # Native admin client + switch lifecycle owner (D12). Isolated from the chat provider: the
    # admin API is only ever reached through this manager, never from the OpenAI-compat path.
    admin = OllamaAdmin.from_config(config)
    manager = ModelManager(config, admin, executor=executor)

    # Apply the active model's per-model settings (and restore/validate it on --resume).
    config = await _activate_model(manager, state, config, console, resume=resume)
    provider = OpenAICompatProvider.from_config(config)
    executor.active_model = state.model

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
            if command.model_pick or command.model_target is not None:
                config, provider = await _switch_model(
                    manager=manager, state=state, config=config, provider=provider,
                    console=console, target=command.model_target,
                )
            if command.toggle_verbose:
                observer.verbose = not observer.verbose
                render.note(console, f"verbose {'on' if observer.verbose else 'off'}")
            if command.show_stats:
                render.note(console, executor.stats_report())
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
                gate=gate,
                approver=approver,
            )
        )
        # Block a model switch while a turn is in flight (context.md: REPL-idle only). Belt-and-
        # suspenders: /model is handled between turns anyway, but this makes the guard explicit.
        manager.busy = True
        try:
            await task
        except KeyboardInterrupt:  # Ctrl+C mid-turn: cancel, keep the session
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, KeyboardInterrupt):
                pass
            render.note(console, "cancelled")
        finally:
            manager.busy = False

        state.save(project_dir)

    if executor.stats.attempts:
        render.note(console, executor.stats_report())
    render.note(console, "bye")
