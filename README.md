# AresCode

**A local-first terminal coding agent powered by Ollama.**

AresCode brings the Claude Code-style workflow to local models: open a repository, describe the
change you want, review the proposed file edits or shell commands, and keep the work inside your
own machine. It is designed around a small, reliable tool surface and a deny-first permission gate
so weaker local models can still operate through a robust harness.

> Status: MVP complete. Phases 0-6 are done: package/config, streaming REPL, tools and loop,
> SEARCH/REPLACE editing, the trust/permission layer, the endurance layer (repo map, `ARES.md`
> project memory, token-budget compaction), and polish/packaging. The MVP Definition of Done —
> "fix the failing test," end-to-end on a live local model — is met on both `qwen2.5-coder:7b` and
> `14b-instruct`. Recent hardening: parser recovery of the tiny-file edit shapes weak models emit,
> plus a compact system prompt; see [`implementation/`](implementation/).

## Why AresCode

Cloud coding agents are convenient, but they require sending code to a remote model. AresCode keeps
the agent loop local by targeting Ollama's OpenAI-compatible API. The out-of-the-box default is the
light `qwen2.5-coder:7b` so a first run works on a low-VRAM machine; `qwen2.5-coder:14b-instruct` is
a stronger opt-in you switch to at runtime with `/model`, and that choice is remembered as the
default from the next launch on.

The project favors predictable behavior over a large feature surface:

| Capability | What it does |
|---|---|
| Local model backend | Runs against Ollama by default through `http://localhost:11434/v1`. |
| Interactive REPL | Chat-style terminal UI with streaming responses, history, interrupts, and slash commands. |
| Codebase tools | Read files, write new files, edit existing files, run shell commands, grep, and glob. |
| Robust edits | Uses Aider-style SEARCH/REPLACE blocks with exact, whitespace-normalized, fuzzy, and whole-file fallback paths. |
| Permission gate | Read-only actions auto-allow; writes, edits, and shell commands ask for approval with previews. |
| Hard denials | Path escapes and dangerous command patterns are blocked before execution. |
| Sessions | Save and resume prior conversations with `--resume`. |
| Model switching | Switch installed Ollama models mid-session with `/model` (per-model context settings); your last choice is remembered as the default next launch. |
| Context management | A repo map and optional `ARES.md` project memory are injected into the system prompt; long sessions are compacted at 75% of the token budget by summarizing the oldest history. |

## Quick Start

### 1. Install prerequisites

- Python 3.11+
- [Ollama](https://ollama.com)
- [ripgrep](https://github.com/BurntSushi/ripgrep) (`rg`) on `PATH`
- Git

Pull the default model:

```powershell
ollama pull qwen2.5-coder:7b
```

For stronger instruction-following on a capable GPU, also pull the larger model and switch to it
with `/model` (your choice is remembered next launch):

```powershell
ollama pull qwen2.5-coder:14b-instruct
```

### 2. Install AresCode

To just use it, install with [pipx](https://pipx.pypa.io) so the `arescode` command is available
everywhere in its own isolated environment:

```powershell
pipx install .
arescode --version
```

To hack on AresCode instead, use an editable install:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1      # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
```

### 3. Run inside a project

```powershell
cd path\to\your\project
arescode
```

On startup AresCode checks that the Ollama server is reachable and the model is installed, and prints
the exact `ollama serve` / `ollama pull` command if something is missing — so a first run tells you
what to fix. To probe the server manually you can still run `python scripts/check_ollama.py`.

Useful launch flags:

```powershell
arescode --model qwen2.5-coder:14b-instruct
arescode --ctx 16384
arescode --resume
arescode --yolo
```

`--yolo` auto-approves actions that would normally prompt. Hard denials still apply.

## Using the REPL

Type a task and press Enter. AresCode will stream its response, call tools when needed, and ask for
approval before changing files or running untrusted shell commands.

Common commands:

| Command | Purpose |
|---|---|
| `/help` | Show REPL commands. |
| `/clear` | Reset conversation history. |
| `/init` | Scan the repo and write/update `ARES.md` project memory (model-authored). |
| `/model [name]` | Pick or switch the active model. |
| `/verbose` | Toggle full tool output in the trace. |
| `/stats` | Show edit telemetry grouped by model. |
| `/map` | Show the repository map injected into the system prompt. |
| `/compact` | Summarize older history now to reclaim context budget. |
| `/allow [cmd]` | View or add a session command allowlist entry. |
| `/deny <cmd>` | Remove a command from the session allowlist. |
| `/sessions` | List saved sessions for this project. |
| `/resume <id>` | Load a saved session by id (or a unique id prefix). |
| `/exit`, `/quit` | Leave AresCode. |

Input shortcuts:

| Shortcut | Behavior |
|---|---|
| Enter | Send the message. |
| \ + Enter | Insert a newline (end the line with a backslash to continue). |
| Ctrl+J | Insert a newline (fallback). |
| Esc | Interrupt the current turn and return to the prompt. |
| Ctrl+C | Interrupt the current turn; press twice in a row to exit. |
| Ctrl+D | Exit. |

## Project memory

Drop an `ARES.md` file in your project root to give AresCode durable, project-specific context
(conventions, key commands, gotchas). It is loaded into the system prompt on every turn. To have
AresCode write it for you, run `/init` inside the session: AresCode gathers the project's key files
(README, build config) and the repo map, the model drafts `ARES.md` from them, and you approve the
write like any other. Re-run `/init` any time to refresh it.

AresCode also builds a gitignore-filtered **repo map** at session start so the model knows the
project's shape without spending tool calls. The map (and `ARES.md`) **stay current within a
session**: whenever a tool changes the working tree, the map is rescanned for the next turn — so
files the agent creates, renames, or deletes don't leave the model looking at a stale snapshot.
`/map` shows the map and rescans it live on demand. As a session grows past 75% of the context
budget, the oldest history is summarized into a compact note (or force it with `/compact`) so long,
multi-step tasks stay coherent on a small local model.

## Safety Model

AresCode assumes tool execution must be controlled by the harness, not by model promises.

- Read-only actions (`read_file`, `grep`, `glob`) are allowed automatically.
- File writes and edits show a preview before approval.
- Shell commands prompt unless the first token is on the allowlist.
- The gate blocks path escapes outside the project root.
- Dangerous command patterns such as `sudo`, force-pushes, `.env` or SSH-key exfiltration, and
  `curl ... | sh`-style installs are denied.
- The executor repeats the hard-deny check before running a tool.

Session allowlists reset when the session ends. Persistent allowlists can be configured in
`.arescode.toml`.

## Configuration

Configuration is layered from lowest to highest precedence:

1. Built-in defaults (`qwen2.5-coder:7b`)
2. Remembered model — your last `/model` switch (`~/.arescode/last_model`)
3. `~/.arescode/config.toml`
4. `./.arescode.toml`
5. CLI flags such as `--model` and `--ctx`

The remembered model overrides only the built-in default, so a `model` set in a config file or
passed via `--model` still wins.

Example project config:

```toml
model = "qwen2.5-coder:7b"
base_url = "http://localhost:11434/v1"
num_ctx = 16384
temperature = 0.1
max_steps = 25
request_timeout = 120.0
bash_timeout = 60.0

allow_commands = ["pytest", "git"]
allow_paths = []

[models."qwen2.5-coder:7b"]
num_ctx = 16384
temperature = 0.1

[models."qwen2.5-coder:14b-instruct"]
num_ctx = 6144   # 6GB GPUs (e.g. RTX 3050): 8192 fails to load; 6144 is the measured ceiling
temperature = 0.1
```

Unknown config keys are rejected so typos fail loudly.

## Architecture

AresCode is a single-threaded agent loop:

```text
user prompt
  -> assemble context
  -> stream model response
  -> parse tool actions
  -> permission gate
  -> execute approved tools
  -> append observations
  -> repeat until the model stops calling tools
```

The core implementation lives in:

| Area | Path |
|---|---|
| CLI entrypoint | `src/arescode/main.py` |
| REPL and slash commands | `src/arescode/ui/repl.py` |
| Agent loop | `src/arescode/core/loop.py` |
| Parser | `src/arescode/core/parser.py` |
| Context (system prompt, budget, compaction) | `src/arescode/core/context.py` |
| Repo map | `src/arescode/repo/repomap.py` |
| Tool registry | `src/arescode/tools/registry.py` |
| Edit engine | `src/arescode/tools/edit.py` |
| Permission gate | `src/arescode/permissions/gate.py` |
| Configuration | `src/arescode/config.py` |

For deeper design notes, read [`docs/context.md`](docs/context.md). For setup and operational
details, read [`docs/STARTUP.md`](docs/STARTUP.md).

## Development

Run the checks used during development:

```powershell
ruff check .
pytest
```

Useful targeted commands:

```powershell
pytest tests/test_parser.py
pytest tests/test_edit.py
pytest tests/test_gate.py
python -m arescode
```

The test suite focuses heavily on the parser, edit cascade, permission gate, shell tool, provider,
state/session handling, and model-selection flow.

## Project Roadmap

Completed:

- Phase 0: packaging, config, CI-ready test setup
- Phase 1: streaming terminal REPL and sessions
- Phase 2: tool parser, tool registry, shell/search/file tools, agent loop
- Phase 3: SEARCH/REPLACE editing with fallbacks and telemetry
- Phase 4: deny-first permission gate with previews and hard denials
- Phase 5: repo map, `ARES.md` project memory, token accounting, and summarizing compaction
- Phase 6: polish, packaging (`pipx`), first-run preflight, and the headless dogfood driver

The MVP Definition of Done is met on both `qwen2.5-coder:7b` and `14b-instruct`. Post-MVP hardening —
parser edit-recovery and a compact, measured system prompt — is documented in
[`implementation/`](implementation/); deferred scope lives in [`LATER.md`](LATER.md).

See [`docs/TASKS.md`](docs/TASKS.md) for the implementation plan.

## License

MIT
