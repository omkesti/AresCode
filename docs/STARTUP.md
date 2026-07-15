# STARTUP.md — Running AresCode

Every command needed to set up, run, test, and verify the project, in the order you'd
normally use them. Commands are given for **Windows PowerShell** (primary) with a
**macOS/Linux (bash)** equivalent underneath where they differ.

> Current stage: **Phase 4 (Trust)**. A deny-first **permission gate** now guards every action:
> reads auto-allow, writes/edits and shell prompt for a single-keystroke `y/n/a` (with a change
> preview), and path escapes plus a command blocklist are hard-denied. Session choices can be
> remembered (`a`, or `/allow`); `--yolo` auto-approves. This is on top of Phase 3's SEARCH/REPLACE
> edit cascade, whole-file fallback, colored diffs, and edit telemetry (`/stats`).
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

Pull the target model (14B Q4, ≈9 GB, one time):

```powershell
ollama pull qwen2.5-coder:14b-instruct
```

> On a GPU with <12 GB VRAM, Ollama offloads part of the 14B model to CPU (slower). If generation
> is too slow, either drop `num_ctx` to 8192 or fall back to the smaller `qwen2.5-coder:7b`.

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
`arescode` console command.

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
python scripts/check_ollama.py --model qwen2.5-coder:14b-instruct --ctx 16384 --base-url http://localhost:11434
```

Expected: `PASS` lines for the model and completion, ending in `OK — all checks passed`.

---

## 4. Run the app

With the venv active:

```powershell
arescode
```

Equivalent (no activation needed):

```powershell
python -m arescode
```

Flag overrides and resume:

```powershell
arescode --model qwen2.5-coder:14b-instruct --ctx 16384   # override model / context window
arescode --resume                               # continue the most recent session
arescode --yolo                                 # auto-approve every action (hard denials still apply)
```

This opens the interactive REPL. Type a message and press **Enter** to send
(**Ctrl+J** inserts a newline). In-REPL commands:

| Command | Effect |
|---|---|
| `/help` | list commands |
| `/clear` | reset the conversation history |
| `/model [name]` | no arg: pick from installed models; with a name: switch (unloads the old model from VRAM first) |
| `/verbose` | toggle full tool output in the trace |
| `/stats` | show edit telemetry for the session (grouped by model) |
| `/allow [cmd]` | no arg: show the allowlist; with a token: always allow that bash command |
| `/deny <cmd>` | remove a bash command from the session allowlist |
| `/exit`, `/quit` | leave (or press **Ctrl+D**) |

When the agent proposes a write, edit, or shell command, the gate prompts with a preview:
press **y** (once), **n** (decline), or **a** (always — remember this file or command for the
session). Path escapes outside the project root and blocklisted commands are refused outright.

**Ctrl+C** cancels the current response without ending the session. Sessions autosave to
`.arescode/sessions/<timestamp>.json` after each reply.

> First reply after a cold start is slow while the 14B model loads into VRAM (longer than the 7B
> did, and slower still if it spills to CPU on a <12 GB GPU); subsequent replies are near-instant
> because `keep_alive` keeps it warm.

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

1. Built-in defaults (in `src/arescode/config.py`)
2. `~/.arescode/config.toml` — global, all projects
3. `./.arescode.toml` — per project (repo root)
4. CLI flags (`--model`, `--ctx`)

Example `./.arescode.toml`:

```toml
model = "qwen2.5-coder:14b-instruct"
# model = "qwen2.5-coder:7b"        # faster, weaker fallback for low-VRAM machines
base_url = "http://localhost:11434/v1"
num_ctx = 16384                     # default window; 14B Q4 ≈ 9GB, KV cache grows with num_ctx
temperature = 0.1
max_steps = 25
request_timeout = 120.0
bash_timeout = 60.0

# Persistent permission allowlists (Phase 4). Bash first-tokens and file paths listed here are
# auto-approved without prompting, in every session for this project.
allow_commands = ["pytest", "ls", "git"]
allow_paths = []

# Per-model settings (D12). Switch between these at runtime with /model; the previous model is
# unloaded from VRAM before the next one loads. A model with no section here uses the num_ctx /
# temperature above as its defaults.
[models."qwen2.5-coder:7b"]
num_ctx = 16384                     # ≈4.7GB Q4: weights + KV cache fit VRAM, use the fuller window
temperature = 0.1

[models."qwen2.5-coder:14b-instruct"]
num_ctx = 8192                      # ≈9GB Q4: on a <12GB GPU weights spill to CPU — keep the KV cache small
temperature = 0.1
```

Unknown keys are rejected (a typo fails loudly rather than being ignored) — including inside a
`[models."…"]` section.

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
python -m arescode
python scripts/check_ollama.py
```

macOS / Linux:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
ruff check .
pytest
python -m arescode
python scripts/check_ollama.py
```

---

## 9. Troubleshooting

| Symptom | Fix |
|---|---|
| `ollama: command not found` | Install Ollama and reopen the terminal. |
| `check_ollama.py` → connection refused | Start the server: `ollama serve`. |
| Model missing in `check_ollama.py` | `ollama pull qwen2.5-coder:14b-instruct`. |
| Model feels "dumb" / truncated | Confirm `num_ctx` is set (Ollama silently defaults to 4096). |
| PowerShell won't activate the venv | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then re-activate. |
| `rg: command not found` in a search | Install ripgrep and ensure it's on `PATH`. |
| 14B slow / VRAM thrash on a <12 GB GPU | Ollama offloaded to CPU; lower `num_ctx` (e.g. 8192), or fall back to `qwen2.5-coder:7b`. |
