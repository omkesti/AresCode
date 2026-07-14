# AresCode

A Claude Code–style terminal coding agent powered by **local models via Ollama**
(primary target: `qwen2.5-coder:7b`). Single-threaded agent loop, text-based action
protocol, lenient parsing — the reliability lives in the harness, not the model.

See [`docs/context.md`](docs/context.md) for the full architecture and
[`docs/TASKS.md`](docs/TASKS.md) for the phased implementation plan.

## Status

Early development. **Phase 3 (Edit) complete:** the agent now edits code — SEARCH/REPLACE with
an exact→whitespace→fuzzy matching cascade, whole-file fallback, a syntax guard, colored diffs,
and edit telemetry (`/stats`) — on top of Phase 2 (tools + loop), Phase 1 (streaming REPL,
sessions), and Phase 0 (package, config, CI).

Run `arescode` and ask it to fix a test or add a function; `arescode --resume` continues your
last session.

> **Heads-up:** Phase 3 applies edits directly (showing a diff). The permission gate and
> approvals arrive in Phase 4 — until then, run it on a repo with a clean git working tree so
> changes are easy to review and revert.

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
