# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**AresCode** is a Claude Code–style terminal coding agent powered by **local models via Ollama**
(primary target: `qwen2.5-coder:7b`). Package/command/import name is `arescode` (lowercase); the
brand is "AresCode".

`docs/context.md` is the architecture source of truth and `docs/TASKS.md` is the phased plan —
read the relevant section before non-trivial work rather than re-deriving intent. This file
captures what actively shapes day-to-day work.

## Status

- **Phases 0–4 are complete and verified** (M1 Talk, M2 Act, M3 Edit, M4 Trust).
- Working today: layered config, the CLI entry point, an OpenAI-compatible **streaming provider**
  over Ollama, an interactive **REPL** (slash commands, live markdown), **session save/resume**,
  the **lenient parser + read-only tools + master loop**, the **SEARCH/REPLACE edit cascade**
  (retry + whole-file fallback + telemetry), and the **deny-first permission gate** (auto-allow
  reads, ask-with-preview for writes/shell, hard-deny path escapes + a command blocklist, session
  & persistent allowlists, `/allow` `/deny`, and `--yolo`).
- **Next is Phase 5 (Endure)** — repo map, `ARES.md` memory, token-budget compaction.

Implemented modules: `config.py`, `main.py`, `providers/*`, `core/state.py`, `core/parser.py`,
`core/loop.py`, `core/context.py` (system-prompt loader only), `tools/*`, `permissions/gate.py`,
`ui/repl.py`, `ui/render.py`, `ui/approve.py`. Still a **docstring-only stub** awaiting its phase:
`repo/repomap.py` (Phase 5); `core/context.py` gains repo map + compaction in Phase 5.

## Commands

Setup (one time):

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1      # Windows PowerShell (macOS/Linux: source .venv/bin/activate)
pip install -e ".[dev]"
```

Day to day (with the venv active; otherwise prefix with `.\.venv\Scripts\python.exe -m`):

```bash
arescode                          # launch the REPL (also: python -m arescode)
arescode --model <tag> --ctx N    # override model / context window
arescode --resume                 # continue the most recent session
ruff check .                      # lint (ruff check . --fix to auto-fix)
pytest                            # full test suite
pytest tests/test_provider.py     # a single file
pytest tests/test_state.py::test_save_and_load_roundtrip   # a single test
pytest -k override                # tests matching a keyword
python scripts/check_ollama.py    # verify the local Ollama server (endpoint + model + num_ctx)
```

`docs/STARTUP.md` has the full runbook. Tests never need a live model (the provider is tested with
`httpx.MockTransport`); the fixture repo under `tests/fixtures/` is excluded from collection via
`--ignore=tests/fixtures`.

## The one constraint everything follows from

**The target model is drastically weaker than frontier models.** Claude Code gets away with a
minimal harness because Claude is smart; a 7B model is not. So AresCode is deliberately
**harness-heavy**, and the litmus test for every decision is:

> "Would this work if the model gets it 80% right?" If a design needs 99% model accuracy, fix the
> harness, not the prompt.

That means: training-familiar text output protocols (not free-form JSON), lenient forgiving
parsers, retry-with-error-feedback on failed edits, a tiny 6-tool surface, and aggressive context
discipline. Do not "simplify" the harness by trusting the model to be reliable — reliability is
supposed to live in the harness.

## Architecture in brief (see `docs/context.md` §3–4)

- **Single-threaded master loop, one flat message history** (Claude Code's `nO` model): assemble
  context → model call → lenient parse → permission gate → execute → append truncated result →
  repeat, with a hard step cap (default 25). Plain text with no actions ends the turn. No
  sub-agents, no graph frameworks, no LangChain/LangGraph/LiteLLM.
- **Text action protocol, NOT native JSON tool calling** (D2). Simple tools use flat XML-ish tags;
  edits use **Aider-style SEARCH/REPLACE blocks** with a whole-file fallback.
- **Six tools only** (MVP): `read_file`, `write_file` (new files only), `edit_file`, `bash`,
  `grep`, `glob`. Every added tool measurably dilutes a small model's instruction-following.
- **Provider** (`providers/openai_compat.py`): async httpx SSE against `/v1/chat/completions`;
  `providers/base.py` is an ABC (abstract `chat` + shared `complete`). Portable to LM Studio / vLLM
  / cloud by config (D5) — Ollama-specific `options`/`keep_alive` go in the request body.
- **Deny-first permission gate** (Phase 4): auto-allow reads; ask-with-diff for writes; ask for
  shell. Path escapes and blocklisted commands are hard-denied. The gate reads only the *parsed
  action*, never model prose (prompt-injection containment).

## Rules that override normal defaults

- **`core/loop.py`, `core/parser.py`, and the edit cascade in `tools/edit.py` are hand-written by
  the author, not AI-generated** (decision D10 — explicit skill-building goal). Do not autonomously
  author these; offer review/pairing and write their tests instead.
- **`parser.py` and `edit.py` get the deepest test coverage** — table-driven tests over a corpus of
  real malformed model outputs collected during development (`tests/fixtures/model_outputs/`). This
  is where 7B unreliability concentrates; treat coverage here as non-negotiable.
- **`num_ctx` must always be set explicitly** on Ollama calls (default 16384). Ollama silently
  defaults to 4096 regardless of model capability, which truncates context and masquerades as "the
  model is dumb." A reasoning failure? Check `num_ctx` first.
- **REPL input: Enter sends; Ctrl+J (or Alt+Enter) inserts a newline** — a deliberate inversion of
  the default prompt_toolkit multiline binding (and of the literal wording in TASKS 1.3), because
  Windows Terminal swallows Alt+Enter as its fullscreen toggle. Don't revert it.
- **Project memory file is `ARES.md`** (AresCode's equivalent of CLAUDE.md), loaded into the system
  prompt when present; Phase 5 adds support and an `arescode init` template generator.
- Respect the **MVP scope contract** (`docs/context.md` §1): sub-agents, MCP, TODO planner,
  tree-sitter maps, multi-model routing, embeddings/RAG, and hooks are deferred. New scope goes to a
  `LATER.md`, not into the MVP.

## Build order (`docs/TASKS.md`)

M1 Talk ✓ → M2 Act ✓ (parser `[HAND]` + read-only tools + loop `[HAND]`) → M3 Edit ✓
(SEARCH/REPLACE cascade `[HAND]` + retry + whole-file fallback; the make-or-break milestone) →
M4 Trust ✓ (gate) → **M5 Endure** (compaction, ARES.md, repo map) → M6 Polish. Each phase ships
usable and adds tests before its exit check; don't start the next phase until the current one
passes on a real repo.
