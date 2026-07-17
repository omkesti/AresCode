"""Interactive REPL: prompt_toolkit input, slash commands, and the agent loop.

Enter sends; a trailing backslash before Enter (or Ctrl+J) inserts a newline. Each message
runs the agent loop (the model can read files, search, and run commands), rendered as a tool
trace. Esc or Ctrl+C interrupts the current turn without killing the session; two Ctrl+C in a
row (or Ctrl+D) exits (context.md §3, TASKS 1.3 / 1.6 / 2.8).
"""

from __future__ import annotations

import asyncio
import sys
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from rich.console import Console

from arescode.config import BUILTIN_DEFAULT_MODEL, Config, read_last_model, save_last_model
from arescode.core.context import assemble_system_prompt, compact_now
from arescode.core.loop import run_turn
from arescode.core.models import ModelManager, match_model
from arescode.core.state import SessionState
from arescode.permissions.gate import Gate
from arescode.providers.ollama_admin import AdminUnavailable, ModelLoadError, OllamaAdmin
from arescode.providers.openai_compat import OpenAICompatProvider
from arescode.repo.repomap import build_repo_map
from arescode.tools.registry import Executor
from arescode.ui import render, theme
from arescode.ui.approve import auto_approver, interactive_approver
from arescode.ui.model_select import free_text_model, pick_model

# The input prompt, Claude Code-style: a bare '>' in the brand purple. prompt_toolkit
# formatted-text tuples, since rich markup means nothing to PromptSession.
PROMPT_MESSAGE = [("", "\n"), (f"fg:{theme.PRIMARY} bold", "> ")]

# Two Ctrl+C within this many seconds exits AresCode; a lone Ctrl+C only cancels the current turn
# (or, at an idle prompt, arms the exit and prints a hint).
DOUBLE_INTERRUPT_WINDOW = 1.5

HELP_TEXT = """\
Commands:
  /help            show this help
  /clear           reset the conversation history
  /model [name]    no arg: pick from installed models; with a name: switch (unloads the old one).
                   The chosen model is remembered as the default for the next launch.
  /verbose         toggle full tool output in the trace
  /stats           show edit telemetry for this session (grouped by model)
  /map             show the repository map injected into the system prompt
  /compact         summarize older history now to reclaim context budget
  /allow [cmd]     no arg: show the allowlist; with a token: always allow that bash command
  /deny <cmd>      remove a bash command from the session allowlist
  /exit, /quit     leave AresCode
Input:
  Enter            send the message
  \\ + Enter        insert a newline (end the line with a backslash to continue)
  Ctrl+J           insert a newline (also Alt+Enter where the terminal allows it)
  Esc              interrupt the current turn and return to the prompt
  Ctrl+C           interrupt the current turn; press twice in a row to exit
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
    show_map: bool = False  # set when /map is issued
    compact: bool = False  # set when /compact is issued (force a compaction now)
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
    if name == "/map":
        return Command(action="continue", show_map=True)
    if name == "/compact":
        return Command(action="continue", compact=True)
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
    """Enter sends; a trailing backslash before Enter (or Ctrl+J) inserts a newline.

    prompt_toolkit's default multiline binding is the opposite (Enter=newline,
    Alt+Enter=submit), which is unintuitive for chat and unusable on Windows Terminal,
    where Alt+Enter is intercepted as the fullscreen toggle and never reaches the app.
    So Enter submits, and to continue onto a new line you end the line with a backslash
    (shell-style ``\\`` + Enter, which consumes the backslash). Ctrl+J stays bound as a
    terminal-reliable fallback for a bare newline.
    """
    kb = KeyBindings()

    @kb.add("enter")
    def _submit(event) -> None:
        buffer = event.current_buffer
        # Shell-style line continuation: a backslash immediately before the cursor turns
        # Enter into a newline and is itself consumed, so `\<Enter>` starts a new line.
        if buffer.document.char_before_cursor == "\\":
            buffer.delete_before_cursor(1)
            buffer.insert_text("\n")
        else:
            buffer.validate_and_handle()

    @kb.add("c-j")  # Ctrl+J: reliable bare newline in every terminal
    @kb.add("escape", "enter")  # Alt+Enter: newline where the terminal delivers it
    def _newline(event) -> None:
        event.current_buffer.insert_text("\n")

    return kb


async def _watch_for_escape() -> None:
    """Resolve as soon as the user presses Esc.

    Runs only while a turn is in flight — never concurrently with prompt_toolkit — so it can own
    stdin for the duration. Other keystrokes typed while the model works are swallowed (they would
    otherwise leak into the next prompt). If keys can't be watched here (stdin isn't an interactive
    terminal, or the platform read fails) it waits forever, disabling Esc but leaving Ctrl+C as the
    escape hatch.
    """
    try:
        if sys.platform == "win32":
            await _watch_for_escape_windows()
        else:
            await _watch_for_escape_posix()
    except (OSError, ValueError, ImportError):
        await asyncio.Event().wait()


async def _watch_for_escape_windows() -> None:
    import msvcrt

    esc = "\x1b"
    while True:
        while msvcrt.kbhit():
            ch = msvcrt.getwch()
            if ch == esc:
                return
            if ch in ("\x00", "\xe0") and msvcrt.kbhit():
                msvcrt.getwch()  # drop the second half of a function/arrow key
        await asyncio.sleep(0.02)


async def _watch_for_escape_posix() -> None:
    import os
    import termios
    import tty

    if not sys.stdin.isatty():
        await asyncio.Event().wait()
        return
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    loop = asyncio.get_running_loop()
    seen = asyncio.Event()

    def _on_readable() -> None:
        try:
            data = os.read(fd, 1024)
        except OSError:
            data = b""
        if b"\x1b" in data:
            seen.set()

    try:
        tty.setcbreak(fd)
        loop.add_reader(fd, _on_readable)
        await seen.wait()
    finally:
        loop.remove_reader(fd)
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


async def _await_turn(task: asyncio.Future) -> str:
    """Await a running turn, cancelling it if the user presses Esc.

    Returns ``"escaped"`` when Esc interrupted the turn, else ``"done"`` (re-raising whatever the
    turn itself raised). A Ctrl+C (KeyboardInterrupt) propagates to the caller, which owns the
    cancel-vs-exit decision.
    """
    watcher = asyncio.ensure_future(_watch_for_escape())
    try:
        done, _ = await asyncio.wait({task, watcher}, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            await task  # surface the turn's result / exception
            return "done"
        task.cancel()  # Esc fired first
        with suppress(asyncio.CancelledError, KeyboardInterrupt):
            await task
        return "escaped"
    finally:
        watcher.cancel()
        with suppress(asyncio.CancelledError, KeyboardInterrupt):
            await watcher


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
    active = await _verify_remembered_or_fallback(manager, active, console)
    state.model = active
    return manager.effective_config(active)


async def _verify_remembered_or_fallback(
    manager: ModelManager, active: str, console: Console
) -> str:
    """Self-heal a poisoned remembered default (D13): if we're about to launch on a machine-
    remembered model, verify it actually loads; if it can't (e.g. too large for VRAM, crashing
    the backend), fall back to the built-in default and forget the remembered choice.

    Scoped to exactly the self-poisoning case — a ``/model`` switch persists the choice, so a model
    that crashes on load would otherwise take down the first turn on *every* future launch. A model
    chosen explicitly (``--model`` / config / a resumed session) is left alone: that's the user's
    call, and there is nothing persisted to un-poison.
    """
    if read_last_model() != active:
        return active  # not the remembered default -> not our self-poisoning case
    if active == BUILTIN_DEFAULT_MODEL:
        return active  # already the safe harbor; nothing safer to fall back to
    try:
        render.note(console, f"verifying {active} loads...")
        await manager.verify_loads(active)
    except ModelLoadError as exc:
        render.error(console, f"{active} failed to load: {exc}")
        render.note(
            console,
            f"falling back to {BUILTIN_DEFAULT_MODEL} and forgetting the remembered default "
            f"(switch back with /model once there is enough free VRAM).",
        )
        save_last_model(BUILTIN_DEFAULT_MODEL)
        return BUILTIN_DEFAULT_MODEL
    except AdminUnavailable:
        pass  # can't verify without the native API — trust it and let it load lazily, as before
    return active


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
        f"[{theme.ACCENT_BOLD}]{result.model}[/]  num_ctx={result.num_ctx}  "
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
    # Built once at session start and injected into the system prompt (context.md §4.5, TASKS 5.1);
    # kept around so /map can redisplay it without a rescan.
    repo_map = build_repo_map(project_dir)
    system_prompt = assemble_system_prompt(project_dir, repo_map=repo_map)
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

    # Timestamp of the last Ctrl+C, shared across the prompt and the in-turn handler: a second
    # Ctrl+C within DOUBLE_INTERRUPT_WINDOW exits, so "double Ctrl+C" works from either context.
    last_interrupt = 0.0

    while True:
        try:
            user_input = await prompt_session.prompt_async(PROMPT_MESSAGE)
        except EOFError:  # Ctrl+D still exits
            break
        except KeyboardInterrupt:  # Ctrl+C at the prompt: press again quickly to exit
            now = time.monotonic()
            if now - last_interrupt < DOUBLE_INTERRUPT_WINDOW:
                break
            last_interrupt = now
            render.note(console, "press Ctrl+C again to exit")
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
            if command.show_map:
                render.note(console, repo_map or "(repository map is empty)")
            if command.compact:
                result = await compact_now(state, provider=provider, num_ctx=config.num_ctx)
                if result.compacted:
                    render.note(
                        console,
                        f"compacted history: folded {result.folded} message(s) "
                        f"(~{result.before_tokens}->{result.after_tokens} tokens)",
                    )
                    state.save(project_dir)
                else:
                    render.note(console, f"nothing to compact ({result.reason})")
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
                num_ctx=config.num_ctx,
            )
        )
        # Block a model switch while a turn is in flight (context.md: REPL-idle only). Belt-and-
        # suspenders: /model is handled between turns anyway, but this makes the guard explicit.
        manager.busy = True
        should_exit = False
        try:
            # Esc interrupts the turn and drops back to the prompt (races a key-watcher against the
            # turn and cancels it on Esc). Ctrl+C also interrupts; a quick second Ctrl+C exits.
            if await _await_turn(task) == "escaped":
                render.note(console, "interrupted (Esc)")
        except KeyboardInterrupt:  # Ctrl+C mid-turn: cancel, keep the session
            task.cancel()
            with suppress(asyncio.CancelledError, KeyboardInterrupt):
                await task
            now = time.monotonic()
            if now - last_interrupt < DOUBLE_INTERRUPT_WINDOW:
                should_exit = True  # second Ctrl+C in quick succession -> leave
            else:
                last_interrupt = now
                render.note(console, "cancelled (Ctrl+C again to exit)")
        finally:
            manager.busy = False

        if should_exit:
            break
        state.save(project_dir)

    if executor.stats.attempts:
        render.note(console, executor.stats_report())
    render.note(console, "bye")
