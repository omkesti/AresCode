"""Interactive REPL: prompt_toolkit input, slash commands, and the agent loop.

Enter sends; a trailing backslash before Enter (or Ctrl+J) inserts a newline. Each message
runs the agent loop (the model can read files, search, and run commands), rendered as a tool
trace. Esc or Ctrl+C interrupts the current turn without killing the session; two Ctrl+C in a
row (or Ctrl+D) exits (context.md §3, TASKS 1.3 / 1.6 / 2.8).
"""

from __future__ import annotations

import asyncio
import re
import shutil
import sys
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from rich.console import Console

from arescode.config import BUILTIN_DEFAULT_MODEL, Config, read_last_model, save_last_model
from arescode.core.context import (
    ARES_MEMORY_FILENAME,
    INIT_SYSTEM_PROMPT,
    INIT_USER_TEMPLATE,
    assemble_system_prompt,
    compact_now,
    gather_init_context,
)
from arescode.core.loop import run_turn
from arescode.core.models import ModelManager, match_model
from arescode.core.state import SessionInfo, SessionState
from arescode.permissions.gate import Decision, Gate
from arescode.providers.base import ProviderError
from arescode.providers.ollama_admin import AdminUnavailable, ModelLoadError, OllamaAdmin
from arescode.providers.openai_compat import OpenAICompatProvider
from arescode.repo.repomap import build_repo_map
from arescode.tools.registry import EditFileAction, Executor, SearchReplace, WriteFileAction
from arescode.ui import render, theme
from arescode.ui.approve import auto_approver, interactive_approver
from arescode.ui.model_select import free_text_model, pick_model

# The input box chrome (UX tasks 1-2): the prompt sits between two dim horizontal rules — a top
# rule carried in the prompt message and a bottom rule pinned just under the input via
# prompt_toolkit's bottom_toolbar. Because the toolbar stays put while the buffer above it grows,
# the box expands upward (the top rule slides up) as the input wraps onto more lines. Both are
# callables, re-evaluated every render, so the rules track the terminal width through live resizes.
# Formatted-text tuples, since rich markup means nothing to PromptSession.
RULE_CHAR = "─"


def _hrule() -> str:
    """A horizontal rule string sized to the current terminal width."""
    return RULE_CHAR * max(1, shutil.get_terminal_size((80, 24)).columns)


def _prompt_message() -> list[tuple[str, str]]:
    """Prompt text: a blank spacer, the top rule on its own line, then the brand-purple '> '.

    prompt_toolkit splits a multiline message at the last newline: everything before it renders as
    the block above the input (giving the rule its own line) and the trailing '> ' becomes the
    inline prefix on the first input line.
    """
    return [
        ("", "\n"),
        (f"fg:{theme.SHADOW}", _hrule()),
        ("", "\n"),
        (f"fg:{theme.PRIMARY} bold", "> "),
    ]


def _bottom_rule() -> list[tuple[str, str]]:
    """The bottom rule of the input box; a bottom_toolbar, so it stays put as the input grows."""
    return [(f"fg:{theme.SHADOW}", _hrule())]


# Drop prompt_toolkit's default reverse-video bar so the bottom_toolbar reads as a thin rule.
_PROMPT_STYLE = Style.from_dict(
    {
        "bottom-toolbar": f"noreverse fg:{theme.SHADOW} bg:default",
        "bottom-toolbar.text": f"noreverse fg:{theme.SHADOW} bg:default",
    }
)

# Two Ctrl+C within this many seconds exits AresCode; a lone Ctrl+C only cancels the current turn
# (or, at an idle prompt, arms the exit and prints a hint).
DOUBLE_INTERRUPT_WINDOW = 1.5

HELP_TEXT = """\
Commands:
  /help            show this help
  /clear           reset the conversation history
  /init            scan the repo and write/update ARES.md (project memory), model-authored
  /model [name]    no arg: pick from installed models; with a name: switch (unloads the old one).
                   The chosen model is remembered as the default for the next launch.
  /verbose         toggle full tool output in the trace
  /stats           show edit telemetry for this session (grouped by model)
  /map             rescan the project and show the repo map (also refreshes what the model sees)
  /compact         summarize older history now to reclaim context budget
  /allow [cmd]     no arg: show the allowlist; with a token: always allow that bash command
  /deny <cmd>      remove a bash command from the session allowlist
  /sessions        list saved sessions for this project (id, messages, model)
  /resume <id>     load a saved session by id (or a unique id prefix) into this one
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
    init: bool = False  # set when /init is issued -> model-driven ARES.md authoring turn
    toggle_verbose: bool = False  # set when /verbose is issued
    show_stats: bool = False  # set when /stats is issued
    show_map: bool = False  # set when /map is issued
    compact: bool = False  # set when /compact is issued (force a compaction now)
    show_allow: bool = False  # set when /allow is issued with no argument
    allow: str | None = None  # bash token to add to the session allowlist (/allow <cmd>)
    deny: str | None = None  # bash token to drop from the session allowlist (/deny <cmd>)
    show_sessions: bool = False  # set when /sessions is issued -> list saved sessions
    resume_id: str | None = None  # session id (or prefix) to load (/resume <id>)


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
        render.cleared(console)
        return Command(action="continue")
    if name == "/init":
        return Command(action="continue", init=True)
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
    if name == "/sessions":
        return Command(action="continue", show_sessions=True)
    if name == "/resume":
        # The load (and any needed model note) happens in the REPL where `state` is in scope;
        # parse_command only carries the requested id. No arg -> show usage.
        if not arg:
            render.note(console, "usage: /resume <session-id>  (see /sessions)")
            return Command(action="continue")
        return Command(action="continue", resume_id=arg.split()[0])
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


def _build_prompt(project_dir: Path) -> tuple[str, str]:
    """Freshly scan the project: returns ``(repo_map, assembled system prompt)``.

    Called at session start and again whenever the working tree may have changed, so the repo map
    and ``ARES.md`` the model sees stay current *within* a session — not only across launches.
    """
    repo_map = build_repo_map(project_dir)
    return repo_map, assemble_system_prompt(project_dir, repo_map=repo_map)


_MAP_SIZE_SUFFIX = re.compile(r"  \d+(?:\.\d+)?[BKM]$")


def _map_structure(repo_map: str) -> list[str]:
    """The map's shape (file/dir names) with per-file sizes stripped.

    Editing a file changes its rendered size but not the project's structure; comparing structure
    lets the REPL announce a genuine add/rename/delete without crying "changed" on every in-place
    edit.
    """
    return [_MAP_SIZE_SUFFIX.sub("", line) for line in repo_map.splitlines()]


# A whole-document code fence a model may wrap ARES.md in despite being told not to.
_OUTER_FENCE = re.compile(r"^```[^\n]*\n(.*?)\n?```$", re.DOTALL)


def _clean_ares_content(text: str) -> str:
    """Strip a whole-document code fence the writer model sometimes adds around ARES.md.

    Only an *outer* fence wrapping the entire response is removed (anchored match), so a legitimate
    fenced example inside the document is left intact. The approval preview is the final backstop
    against anything odd slipping through.
    """
    text = text.strip()
    fenced = _OUTER_FENCE.match(text)
    return fenced.group(1).strip() if fenced else text


def _format_sessions(infos: list[SessionInfo], current_id: str) -> str:
    """Render the saved-session list for ``/sessions``; the active session is marked.

    Pure string builder (no console) so it is unit-testable. Columns: id, message count, model,
    and creation time; the current session gets a ``*`` marker and a ``(current)`` suffix.
    """
    if not infos:
        return "no saved sessions for this project yet"
    lines = [f"Saved sessions ({len(infos)}):"]
    id_width = max(len(i.session_id) for i in infos)
    for info in infos:
        marker = "*" if info.session_id == current_id else " "
        created = info.created_at or "?"
        suffix = "  (current)" if info.session_id == current_id else ""
        lines.append(
            f"  {marker} {info.session_id:<{id_width}}  "
            f"{info.message_count:>3} msg  {info.model or '?'}  {created}{suffix}"
        )
    lines.append("resume one with /resume <id> (a unique id prefix works too)")
    return "\n".join(lines)


def _startup_warnings(
    model: str,
    base_url: str,
    installed: list[str] | None,
    *,
    server_unreachable: bool,
    rg_present: bool,
) -> str:
    """Build the first-run warning text (empty when all is well) — pure, so it is unit-testable.

    ``installed`` is the model list from the server, or ``None`` when it couldn't be determined
    (a non-Ollama backend whose native API 404s — we then can't check the model, so we stay quiet
    about it). ``server_unreachable`` is set only for a connection-level failure (the server is
    down). Each problem is stated with the exact command that fixes it (TASKS 6.2).
    """
    blocks: list[str] = []
    if server_unreachable:
        blocks.append(
            f"Can't reach the model server at {base_url}.\n"
            f"  -> Start Ollama with:  ollama serve\n"
            f"  (or set base_url in .arescode.toml if your backend lives elsewhere)"
        )
    elif installed is not None and match_model(model, installed).model is None:
        block = (
            f"Model '{model}' is not installed on the server.\n"
            f"  -> Pull it with:  ollama pull {model}"
        )
        if installed:
            block += f"\n  Installed: {', '.join(sorted(installed))}"
        blocks.append(block)
    if not rg_present:
        blocks.append(
            "ripgrep (rg) is not on PATH — grep will use a slower built-in fallback.\n"
            "  -> For faster search, install ripgrep: https://github.com/BurntSushi/ripgrep"
        )
    return "\n".join(blocks)


async def _preflight(manager: ModelManager, model: str, base_url: str, console: Console) -> None:
    """Best-effort first-run check: warn (with exact fixes) if the server is down, the configured
    model is missing, or ripgrep is absent. Never fatal — a transient issue shouldn't block the
    launch, and a non-Ollama backend simply can't be probed this way (context.md §4.7, TASKS 6.2).
    """
    installed: list[str] | None = None
    server_unreachable = False
    try:
        installed = [m.name for m in await manager.admin.list_installed()]
    except AdminUnavailable as exc:
        # status is None -> connection-level failure (server down); a set status (e.g. 404) means
        # the native API is absent (non-Ollama backend), which is not an error we can act on.
        server_unreachable = exc.status is None
    text = _startup_warnings(
        model, base_url, installed,
        server_unreachable=server_unreachable, rg_present=shutil.which("rg") is not None,
    )
    if not text:
        return
    console.print()
    for line in text.splitlines():
        # Headline lines (flush-left) stand out; indented fix/detail lines are dimmed.
        console.print(f"[dim]{line}[/dim]" if line.startswith(" ") else f"[yellow]{line}[/yellow]")


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
    console.clear()  # start the session from a clean, top-anchored screen (UX task 3)
    gate = Gate.from_config(project_dir, config)
    approver = auto_approver(console) if yolo else interactive_approver(console)
    # The loop runs the interactive gate (allow/ask/approve); the executor shares the same gate as
    # a hard-deny backstop so a blocklisted command or path escape can never slip through.
    executor = Executor(project_dir, config, gate=gate)
    observer = render.ConsoleObserver(console, verbose=False)
    # Assembled at session start and injected into the system prompt (context.md §4.5, TASKS 5.1);
    # rebuilt within the session whenever a tool touches the tree, and rescanned live by /map.
    repo_map, system_prompt = _build_prompt(project_dir)
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

    # First-run check: surface a down server / missing model / absent ripgrep with the exact fix,
    # before the user types and hits it as a turn error (TASKS 6.2). Best-effort, never fatal.
    # Printed under the banner so the wordmark stays anchored at the top (UX task 3).
    await _preflight(manager, state.model, config.base_url, console)

    if yolo:
        console.print(
            "[bold red]--yolo: every action auto-approved. "
            "Hard-denied commands and path escapes are still blocked.[/bold red]"
        )

    prompt_session: PromptSession = PromptSession(
        history=FileHistory(str(_history_path())),
        multiline=True,
        key_bindings=_build_key_bindings(),
        bottom_toolbar=_bottom_rule,
        style=_PROMPT_STYLE,
    )

    # Timestamp of the last Ctrl+C, shared across the prompt and the in-turn handler: a second
    # Ctrl+C within DOUBLE_INTERRUPT_WINDOW exits, so "double Ctrl+C" works from either context.
    last_interrupt = 0.0

    def _refresh_prompt_if_tree_changed(fs_gen_before: int) -> None:
        """After anything that may have touched the tree, rescan so the next turn's system prompt
        reflects new/renamed/deleted files and any ARES.md write, not the stale snapshot. Announce
        only a real structural change, not size churn from in-place edits."""
        nonlocal system_prompt, repo_map
        if executor.fs_generation != fs_gen_before:
            new_map, new_prompt = _build_prompt(project_dir)
            if _map_structure(new_map) != _map_structure(repo_map):
                render.note(console, "project files changed — repo map refreshed")
            repo_map, system_prompt = new_map, new_prompt

    async def _agent_turn(message: str) -> bool:
        """Run one agent turn for ``message``; returns True if the session should exit."""
        nonlocal last_interrupt
        fs_gen_before = executor.fs_generation  # to detect tree changes made during this turn
        task = asyncio.ensure_future(
            run_turn(
                message,
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
            return True
        _refresh_prompt_if_tree_changed(fs_gen_before)
        state.save(project_dir)
        return False

    async def _run_init() -> None:
        """Author ARES.md model-side, harness-gathered and harness-persisted.

        A single completion turns harness-gathered orientation (repo map + key files) into the
        file's Markdown — NOT an agent loop, because weak models reliably write prose but not a
        reliable write_file tool call across an exploration turn. The content is then written
        through the normal gated write path (preview + approval), and the prompt is refreshed so the
        new memory is live immediately.
        """
        render.note(console, "reading key files and drafting ARES.md…")
        context_text = gather_init_context(project_dir, repo_map=repo_map)
        messages = [
            {"role": "system", "content": INIT_SYSTEM_PROMPT},
            {"role": "user", "content": INIT_USER_TEMPLATE.format(context=context_text)},
        ]
        completion = asyncio.ensure_future(provider.complete(messages))
        try:
            with observer.thinking():
                outcome = await _await_turn(completion)
        except KeyboardInterrupt:
            completion.cancel()
            with suppress(asyncio.CancelledError, KeyboardInterrupt):
                await completion
            render.note(console, "/init cancelled")
            return
        except ProviderError as exc:
            render.error(console, f"/init failed: {exc}")
            return
        if outcome == "escaped":
            render.note(console, "/init interrupted (Esc)")
            return

        content = _clean_ares_content(completion.result())
        if not content:
            render.error(console, "/init: the model returned no usable ARES.md content")
            return

        # Persist through the gated write path so the user still previews + approves. write_file
        # refuses to overwrite, so an existing ARES.md is replaced via a whole-file edit.
        exists = (project_dir / ARES_MEMORY_FILENAME).exists()
        action = (
            EditFileAction(ARES_MEMORY_FILENAME, (SearchReplace("", content),))
            if exists
            else WriteFileAction(ARES_MEMORY_FILENAME, content)
        )
        verdict = gate.check(action)
        if verdict.decision is Decision.DENY:
            render.error(console, f"/init blocked: {verdict.reason}")
            return
        fs_gen_before = executor.fs_generation
        if verdict.decision is not Decision.ALLOW:  # writes are ASK: preview + approve
            approval = approver(action, verdict, executor.preview(action))
            if not approval.approved:
                render.note(console, "/init cancelled — ARES.md not written")
                return
        result = executor.run(action)
        if not result.ok:
            render.error(console, f"/init could not write ARES.md: {result.output}")
            return
        render.note(console, f"{'updated' if exists else 'created'} ARES.md — loaded into context")
        _refresh_prompt_if_tree_changed(fs_gen_before)

    while True:
        try:
            user_input = await prompt_session.prompt_async(_prompt_message)
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
            if command.init:
                await _run_init()
            if command.toggle_verbose:
                observer.verbose = not observer.verbose
                render.note(console, f"verbose {'on' if observer.verbose else 'off'}")
            if command.show_stats:
                render.note(console, executor.stats_report())
            if command.show_map:
                # Rescan live so /map doubles as a manual refresh of what the model sees.
                repo_map, system_prompt = _build_prompt(project_dir)
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
            if command.show_sessions:
                render.note(
                    console,
                    _format_sessions(SessionState.list_sessions(project_dir), state.session_id),
                )
            if command.resume_id is not None:
                loaded, err = SessionState.resolve(project_dir, command.resume_id)
                if err is not None:
                    render.error(console, err)
                elif loaded.session_id == state.session_id:
                    render.note(console, "already in that session")
                else:
                    # Resuming loads the *conversation*; the active model/provider stays as-is
                    # (no VRAM hot-swap, no D13 remember side-effect). Keep serving with the current
                    # model so the resumed session's model field reflects what is answering it.
                    recorded = loaded.model
                    loaded.model = state.model
                    state = loaded
                    executor.active_model = state.model
                    msg = f"resumed session {state.session_id} ({len(state.messages)} messages)"
                    if recorded and recorded != state.model:
                        msg += f"; it was recorded under {recorded} — /model to switch"
                    render.note(console, msg)
            continue

        if await _agent_turn(user_input):
            break

    if executor.stats.attempts:
        render.note(console, executor.stats_report())
    render.note(console, "bye")
