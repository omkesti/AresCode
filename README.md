# AresCode

A Claude Code–style terminal coding agent powered by **local models via Ollama**
(primary target: `qwen2.5-coder:7b`). Single-threaded agent loop, text-based action
protocol, lenient parsing — the reliability lives in the harness, not the model.

See [`docs/context.md`](docs/context.md) for the full architecture and
[`docs/TASKS.md`](docs/TASKS.md) for the phased implementation plan.

## Status

Early development. **Phase 1 (Talk) complete:** streaming provider over Ollama's
OpenAI-compatible endpoint, an interactive REPL (multiline input, slash commands,
live markdown rendering), and session save/resume — on top of the Phase 0 foundation
(installable package, layered config, test harness, CI).

Run `arescode` and start chatting; `arescode --resume` continues your last session.

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
