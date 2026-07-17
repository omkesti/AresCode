# AresCode Project Overview

## Development Progress

1. **Setup and Installation**:
   - Create a virtual environment using `python -m venv .venv` and activate it.
   - Install dependencies with `pip install -e ".[dev]"`.

2. **Day-to-Day Commands**:
   - Launch the REPL: `arescode`
   - Override model or context window: `arescode --model <tag> --ctx N`
   - Continue the most recent session: `arescode --resume`
   - Run tests and linting: `ruff check .`, `pytest`

3. **Documentation**:
   - Full runbook available in `docs/STARTUP.md`.

## Constraints

1. **Model Reliability**:
   - The target model is weaker than frontier models, so the harness must be robust.
   - Designs should focus on reliability in the harness rather than the model.

2. **Tool Surface**:
   - Limited to six tools: `read_file`, `write_file`, `edit_file`, `bash`, `grep`, `glob`.

3. **Provider and Permissions**:
   - Use an async HTTPX SSE provider for Ollama.
   - Implement a deny-first permission gate for actions.

## Architecture

1. **Single-Threaded Master Loop**:
   - Assemble context → model call → lenient parse → permission gate → execute → append result.

2. **Text Action Protocol**:
   - Simple tools use flat XML-ish tags; edits use Aider-style SEARCH/REPLACE blocks.

3. **Provider Flexibility**:
   - Portable to LM Studio / vLLM / cloud by config.

## Rules and Best Practices

1. **Hand-Written Code**:
   - `core/loop.py`, `core/parser.py`, and edit cascade in `tools/edit.py` are hand-written.

2. **Test Coverage**:
   - Deep test coverage for `parser.py` and `edit.py` using table-driven tests on real malformed model outputs.

3. **Context Management**:
   - Always set `num_ctx` explicitly on Ollama calls (default 16384).

4. **REPL Input Handling**:
   - Enter sends; a trailing backslash before Enter (`\` + Enter) inserts a newline, with Ctrl+J as
     a fallback for Windows Terminal compatibility.

5. **Project Memory File**:
   - ARES.md loaded into the system prompt when present.
