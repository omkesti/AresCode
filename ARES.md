# ARES.md

## Overview

AresCode is a local-first terminal coding agent powered by Ollama. It runs against a local model via `http://localhost:11434/v1` and provides an interactive REPL for tasks like reading files, editing code, running shell commands, and more.

## Where things are

- **src/arescode/**: Core functionality including context management, loop handling, models, parsing, state, permissions, providers, repo mapping, tools, and UI components.
- **tests/**: Unit tests with fixtures for various functionalities.
- **prompts/system.md**: System prompt used by the agent.
- **docs/**: Documentation including startup instructions, tasks, and context management.

## Key commands

- **Install**: `python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"`
- **Run**: `arescode` (with optional flags like `--model`, `--ctx`, `--resume`, `--yolo`)
- **Test**: `pytest`
- **Lint/Format**: `ruff check src tests`

## Conventions

- New code goes in the appropriate subdirectory under `src/arescode/`.
- Tests are written in `tests/` and named to match the functionality they test.

## Notes

- Ensure Ollama is running locally.
- Use `arescode --yolo` for auto-approval of actions.
- Required environment variables: None explicitly mentioned.
