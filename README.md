# AresCode

A Claude Code–style terminal coding agent powered by **local models via Ollama**
(primary target: `qwen2.5-coder:7b`). Single-threaded agent loop, text-based action
protocol, lenient parsing — the reliability lives in the harness, not the model.

See [`docs/context.md`](docs/context.md) for the full architecture and
[`docs/TASKS.md`](docs/TASKS.md) for the phased implementation plan.

## Status

Early development. **Phase 4 (Trust) complete:** a deny-first **permission gate** now guards every
action — reads auto-allow, writes/edits and shell prompt for a single-keystroke `y/n/a` (with a
change preview), and path escapes outside the project root plus a command blocklist (`sudo`,
`rm -rf /`, `curl … | sh`, force-push, `.env`/`~/.ssh` exfiltration) are hard-denied and
unoverridable by the model. This sits on top of Phase 3 (SEARCH/REPLACE edit cascade + whole-file
fallback + telemetry), Phase 2 (tools + loop), Phase 1 (streaming REPL, sessions), and Phase 0
(package, config, CI).

Run `arescode` and ask it to fix a test or add a function; approve the writes it proposes.
`arescode --resume` continues your last session; `arescode --yolo` auto-approves every action
(hard denials still apply).

## Requirements

- Python 3.11+
- [Ollama](https://ollama.com) running locally with a model pulled:
  `ollama pull qwen2.5-coder:7b`
- [ripgrep](https://github.com/BurntSushi/ripgrep) (`rg`) on `PATH` (used by the search tool)

## Development

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"

ruff check .                       # lint
pytest                             # tests
python -m arescode                 # launch (prints active model + context size)
python scripts/check_ollama.py     # verify the local Ollama server (TASKS 0.6)
```

## Configuration

Settings are layered lowest → highest precedence:

1. Built-in defaults
2. `~/.arescode/config.toml` (global)
3. `./.arescode.toml` (per project)
4. CLI flags (`--model`, `--ctx`)
