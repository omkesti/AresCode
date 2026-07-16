# AresCode

**A local-first terminal coding agent powered by Ollama.**

AresCode brings the Claude Code-style workflow to local models: open a repository, describe the
change you want, review the proposed file edits or shell commands, and keep the work inside your
own machine. It is designed around a small, reliable tool surface and a deny-first permission gate
so weaker local models can still operate through a robust harness.

> Status: early development. Phases 0-4 are complete: package/config, streaming REPL, tools and
> loop, SEARCH/REPLACE editing, and the trust/permission layer. Phase 5 focuses on endurance:
> compaction, project memory, and longer-session behavior.

## Why AresCode

Cloud coding agents are convenient, but they require sending code to a remote model. AresCode keeps
the agent loop local by targeting Ollama's OpenAI-compatible API, with `qwen2.5-coder:14b-instruct`
as the primary model and `qwen2.5-coder:7b` as a faster fallback.

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
| Model switching | Switch installed Ollama models mid-session with `/model`, including per-model context settings. |

## Quick Start

### 1. Install prerequisites

- Python 3.11+
- [Ollama](https://ollama.com)
- [ripgrep](https://github.com/BurntSushi/ripgrep) (`rg`) on `PATH`
- Git

Pull the recommended model:

```powershell
ollama pull qwen2.5-coder:14b-instruct
```

For lower-VRAM machines, pull the smaller fallback:

```powershell
ollama pull qwen2.5-coder:7b
```

### 2. Install AresCode for development

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

macOS/Linux:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 3. Verify Ollama

```powershell
python scripts/check_ollama.py
```

### 4. Run inside a project

```powershell
arescode
```

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
| `/model [name]` | Pick or switch the active model. |
| `/verbose` | Toggle full tool output in the trace. |
| `/stats` | Show edit telemetry grouped by model. |
| `/allow [cmd]` | View or add a session command allowlist entry. |
| `/deny <cmd>` | Remove a command from the session allowlist. |
| `/exit`, `/quit` | Leave AresCode. |

Input shortcuts:

| Shortcut | Behavior |
|---|---|
| Enter | Send the message. |
| Ctrl+J | Insert a newline. |
| Ctrl+C | Cancel the current turn. |
| Ctrl+D | Exit. |

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

1. Built-in defaults
2. `~/.arescode/config.toml`
3. `./.arescode.toml`
4. CLI flags such as `--model` and `--ctx`

Example project config:

```toml
model = "qwen2.5-coder:14b-instruct"
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
num_ctx = 8192
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

Next:

- Phase 5: compaction, project memory, and stronger long-running session behavior
- Phase 6: polish, packaging workflow, and additional operator ergonomics

See [`docs/TASKS.md`](docs/TASKS.md) for the implementation plan.

## License

MIT
