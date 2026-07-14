# STARTUP.md — Running `agentcli`

Every command needed to set up, run, test, and verify the project, in the order you'd
normally use them. Commands are given for **Windows PowerShell** (primary) with a
**macOS/Linux (bash)** equivalent underneath where they differ.

> Current stage: **Phase 1 (Talk)**. `agentcli` starts an interactive REPL: chat with the
> local model with live streaming, slash commands, and session save/resume. Tool use and
> file editing arrive in Phases 2-3.
> See [`TASKS.md`](TASKS.md) for the roadmap and [`context.md`](context.md) for architecture.

---

## 1. Prerequisites

Install these once and confirm each responds:

| Tool | Minimum | Check command |
|---|---|---|
| Python | 3.11+ | `python --version` |
| [Ollama](https://ollama.com) | any recent | `ollama --version` |
| [ripgrep](https://github.com/BurntSushi/ripgrep) (`rg`) | 13+ | `rg --version` |
| Git | any | `git --version` |

Pull the target model (≈4.7 GB, one time):

```powershell
ollama pull qwen2.5-coder:7b
```

Confirm it is installed and the server is up:

```powershell
ollama list
```

If the server is not running, start it (leave it running in its own terminal):

```powershell
ollama serve
```

---

## 2. One-time project setup

From the repository root (`C:\Om\Projects\AresCode`):

### Create an isolated virtual environment

```powershell
python -m venv .venv
```

### Activate it

```powershell
# PowerShell
.\.venv\Scripts\Activate.ps1
```

```bash
# macOS / Linux
source .venv/bin/activate
```

> If PowerShell blocks activation with an execution-policy error, run once:
> `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`

### Install the package (editable) with dev tools

```powershell
pip install -e ".[dev]"
```

This installs runtime deps (`httpx`, `prompt_toolkit`, `rich`, `typer`, `pydantic`,
`pathspec`) plus dev tools (`pytest`, `pytest-asyncio`, `ruff`) and wires up the
`agentcli` console command.

> **Not activating the venv?** Prefix every command with the venv's interpreter instead:
> `.\.venv\Scripts\python.exe -m pip install -e ".[dev]"` (PowerShell) or
> `./.venv/bin/python -m pip install -e ".[dev]"` (bash).

---

## 3. Verify the local Ollama server

Confirms the OpenAI-compatible endpoint responds, the model is present, and a tiny
completion succeeds with a `num_ctx` override (Phase 0 task 0.6):

```powershell
python scripts/check_ollama.py
```

Options:

```powershell
python scripts/check_ollama.py --model qwen2.5-coder:7b --ctx 16384 --base-url http://localhost:11434
```

Expected: `PASS` lines for the model and completion, ending in `OK — all checks passed`.

---

## 4. Run the app

With the venv active:

```powershell
agentcli
```

Equivalent (no activation needed):

```powershell
python -m agentcli
```

Flag overrides and resume:

```powershell
agentcli --model qwen2.5-coder:7b --ctx 16384   # override model / context window
agentcli --resume                               # continue the most recent session
```

This opens the interactive REPL. Type a message and press **Alt+Enter** to send
(Enter inserts a newline). In-REPL commands:

| Command | Effect |
|---|---|
| `/help` | list commands |
| `/clear` | reset the conversation history |
| `/model <name>` | switch the active model |
| `/exit`, `/quit` | leave (or press **Ctrl+D**) |

**Ctrl+C** cancels the current response without ending the session. Sessions autosave to
`.agentcli/sessions/<timestamp>.json` after each reply.

> First reply after a cold start is slow (~10 s) while the 7B model loads into VRAM;
> subsequent replies are near-instant because `keep_alive` keeps it warm.

---

## 5. Run the tests

```powershell
pytest
```

Useful variants:

```powershell
pytest -q                          # quiet
pytest -v                          # verbose, per-test names
pytest tests/test_smoke.py         # a single file
pytest tests/test_smoke.py::test_default_config_values   # a single test
pytest -k "override"               # tests matching a keyword
```

> The fixture repo under `tests/fixtures/` is intentionally excluded from collection
> (`--ignore=tests/fixtures` in `pyproject.toml`) — it exists for the agent to run, not CI.

---

## 6. Lint

```powershell
ruff check .                       # report issues
ruff check . --fix                 # auto-fix what's safe
ruff format .                      # format (optional)
```

---

## 7. Configuration

Settings are layered, lowest → highest precedence:

1. Built-in defaults (in `src/agentcli/config.py`)
2. `~/.agentcli/config.toml` — global, all projects
3. `./.agentcli.toml` — per project (repo root)
4. CLI flags (`--model`, `--ctx`)

Example `./.agentcli.toml`:

```toml
model = "qwen2.5-coder:7b"
base_url = "http://localhost:11434/v1"
num_ctx = 16384
temperature = 0.1
max_steps = 25
request_timeout = 120.0
bash_timeout = 60.0
```

Unknown keys are rejected (a typo fails loudly rather than being ignored).

---

## 8. Full green-check sequence

Run this end-to-end to confirm a clean environment (mirrors CI in
`.github/workflows/ci.yml`):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
ruff check .
pytest
python -m agentcli
python scripts/check_ollama.py
```

macOS / Linux:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
ruff check .
pytest
python -m agentcli
python scripts/check_ollama.py
```

---

## 9. Troubleshooting

| Symptom | Fix |
|---|---|
| `ollama: command not found` | Install Ollama and reopen the terminal. |
| `check_ollama.py` → connection refused | Start the server: `ollama serve`. |
| Model missing in `check_ollama.py` | `ollama pull qwen2.5-coder:7b`. |
| Model feels "dumb" / truncated | Confirm `num_ctx` is set (Ollama silently defaults to 4096). |
| PowerShell won't activate the venv | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then re-activate. |
| `rg: command not found` in a search | Install ripgrep and ensure it's on `PATH`. |
| VRAM thrash on the RTX 3050 (6 GB) | Lower `num_ctx` (e.g. 8192); large KV cache spills to RAM. |
