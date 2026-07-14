# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status: pre-development

This repo currently contains **no code** — only the design spec at `docs/context.md`. That document is the single source of truth for goals, architecture, and every design decision; read it in full before doing anything substantive. This file summarizes the parts that should actively shape how you work here.

The project (`agent-cli`, working name) is a Claude Code–style terminal coding agent powered by **local models via Ollama** (primary target: `qwen2.5-coder:7b`).

## The one constraint everything follows from

**The target model is drastically weaker than frontier models.** Claude Code gets away with a minimal harness because Claude is smart; a 7B model is not. Therefore this project is deliberately **harness-heavy**, and the litmus test for every design decision is:

> "Would this work if the model gets it 80% right?" If a design needs 99% model accuracy, fix the harness, not the prompt.

Concretely this means: strict training-familiar output protocols (not free-form JSON), lenient forgiving parsers, retry-with-error-feedback on every failed edit, a deliberately tiny 6-tool surface, and aggressive context discipline. Do not "simplify" the harness by trusting the model to be reliable — the reliability is supposed to live in the harness.

## Architecture in brief (see `docs/context.md` §3–4 for detail)

- **Single-threaded master loop, one flat message history.** Model on Claude Code's `nO` loop: `assemble_context → model call → lenient parse → permission gate → execute → append truncated result → repeat`, with a hard step cap (default 25). Plain-text response with no actions ends the turn. No sub-agents, no graph frameworks, no LangChain/LangGraph/LiteLLM.
- **Text action protocol, NOT native JSON tool calling** (decision D2). Simple tools use flat XML-ish tags (`<tool>read_file</tool><path>...</path>`); file edits use **Aider-style SEARCH/REPLACE blocks**, with a whole-file fallback for small files or after repeated edit failures.
- **Six tools only** (MVP): `read_file`, `write_file` (new files only), `edit_file`, `bash`, `grep`, `glob`. Adding tools measurably dilutes a small model's instruction-following — do not add a seventh without a strong reason.
- **Deny-first permission gate:** auto-allow reads; ask-with-diff for writes; ask for shell (with a session allowlist). Path escapes outside the project root and a regex command blocklist are hard-denied and never overridable. Tool results are untrusted input — the gate reads only the *parsed action*, never model prose.

## Where things will live (planned layout, `docs/context.md` §6)

`src/agentcli/` with `core/` (`loop.py`, `context.py`, `parser.py`, `state.py`), `providers/` (`base.py`, `openai_compat.py`), `tools/` (`edit.py`, `shell.py`, `search.py`, `files.py`, `registry.py`), `permissions/gate.py`, `repo/repomap.py`, `ui/`. System prompt is versioned at `prompts/system.md`, never hardcoded in Python. Project memory convention is `AGENT.md` (the CLAUDE.md pattern) in a target repo's root.

## Rules that override normal defaults

- **`core/loop.py` and `core/parser.py` are to be hand-written by the author, not AI-generated** (decision D10 — explicit skill-building goal). Do not autonomously write these unless asked; offer review/pairing instead.
- **`parser.py` and `edit.py` get the deepest test coverage in the project** — table-driven tests over a corpus of real malformed model outputs. This is where 7B unreliability concentrates; treat test coverage here as non-negotiable, not optional.
- **`num_ctx` must always be set explicitly** on Ollama calls (default 16384). Ollama silently defaults to 4096 regardless of model capability, which truncates agent context and masquerades as "the model is dumb." This bug will look like a reasoning failure — check `num_ctx` first.
- Respect the **MVP scope contract** (`docs/context.md` §1): sub-agents, MCP, TODO planner, tree-sitter maps, multi-model routing, embeddings/RAG, hooks, and slash-command frameworks are all explicitly deferred. New scope goes to a `LATER.md`, not into the MVP.

## Tooling (intended — nothing is set up yet)

No `pyproject.toml`, tests, or build tooling exist yet. Per the spec the stack is **Python 3.11+** with `httpx`, `prompt_toolkit`, `rich`, `typer`, `pydantic` + TOML config, `pathspec`, stdlib `difflib`, and the `rg` (ripgrep) binary. Tests will use `pytest` under `tests/`; packaging targets `pipx install`. When you scaffold any of this, wire up the exact commands and update this section with the real invocations (including how to run a single test).

## Build order (`docs/context.md` §7)

M1 Talk (provider + streaming REPL) → M2 Act (parser + read/bash/grep/glob + loop) → M3 Edit (SEARCH/REPLACE + retry + whole-file fallback; the make-or-break milestone) → M4 Trust (gate) → M5 Endure (compaction, AGENT.md, repo map, save/resume) → M6 Polish. Each milestone ships usable; don't start M(n+1) before M(n) is dogfooded on a real project.
