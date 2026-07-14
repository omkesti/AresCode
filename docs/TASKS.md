# TASKS.md — Implementation Plan

> Phased task list for `AresCode`. Completing every task below yields the fully working MVP defined in `context.md` §1.
> Each phase ends with an **exit criteria** checkpoint — do not start the next phase until it passes on a real repo.
>
> Legend: `[HAND]` = write manually without AI assistance (per decision D10). Everything else is fair game for Claude Code, but review every generated line against `context.md` decisions D1–D10.

---

## Phase 0 — Foundation & scaffolding

- [x] 0.1 Initialize repo: `pyproject.toml` (Python 3.11+, src layout), ruff + pytest config, `.gitignore`
- [x] 0.2 Create the package skeleton exactly as in `context.md` §6 (empty modules with docstrings stating each module's single responsibility)
- [x] 0.3 Implement `config.py`: pydantic settings model; load order = defaults → `~/.arescode/config.toml` → `./.arescode.toml` → CLI flags. Fields: model name, base_url, num_ctx (default 16384), temperature (default 0.1), max_steps (default 25), timeouts
- [x] 0.4 Implement `main.py` typer entry: `arescode` launches REPL in cwd; `--model`, `--ctx` flag overrides; validate cwd is a directory the user owns
- [x] 0.5 Set up `tests/` with pytest, one smoke test, and a `fixtures/` sample mini-repo
- [x] 0.6 Verify Ollama locally: `qwen2.5-coder:14b-instruct` pulled (7B is the low-VRAM fallback), OpenAI-compat endpoint responding, confirm num_ctx override works (log a warning if server ignores it)

**Exit criteria:** `pipx run` / `python -m arescode` starts, loads config, prints model + context size, exits cleanly. Tests green in CI (GitHub Actions, lint + pytest).

---

## Phase 1 — Talk (provider + streaming REPL) — milestone M1

- [x] 1.1 Define `ModelProvider` protocol in `providers/base.py`: `chat(messages, **opts) -> AsyncIterator[Chunk]`, plus a non-streaming convenience wrapper
- [x] 1.2 Implement `providers/openai_compat.py` with httpx: SSE streaming against Ollama's `/v1/chat/completions`; pass num_ctx/temperature/keep_alive via extra options; connection-error and timeout handling with clear user-facing messages
- [x] 1.3 Build minimal REPL in `ui/repl.py` (prompt_toolkit): multiline input (Alt+Enter submits), input history file, Ctrl+C cancels current generation without killing the session, Ctrl+D exits
- [x] 1.4 Build `ui/render.py` (rich): stream tokens live, then re-render final message as formatted markdown with syntax-highlighted code blocks; spinner while waiting for first token
- [x] 1.5 Implement `core/state.py`: flat message history (system/user/assistant/tool roles), session autosave to `.arescode/sessions/<timestamp>.json`, `--resume` flag loads the latest
- [x] 1.6 Slash commands v1: `/exit`, `/clear` (reset history), `/model <name>`, `/help`

**Exit criteria:** hold a fluid multi-turn conversation with qwen2.5-coder:14b-instruct in the terminal; kill and resume a session; streaming feels instant on the 3050.

---

## Phase 2 — Act (parser + read-only tools + the loop) — milestone M2

- [x] 2.1 `[HAND]` Implement `core/parser.py`: extract `<tool>…</tool>` actions and SEARCH/REPLACE blocks from raw completions per the protocol in `context.md` §4.2–4.3; tolerate marker drift, stray whitespace, misplaced filenames (hunt ±3 lines), multiple actions per response
- [x] 2.2 Build the malformed-output test corpus: `tests/test_parser.py` table-driven over ≥25 real qwen outputs (collect them by prompting the model during development); target every tolerance case in 2.1
- [x] 2.3 Implement `tools/registry.py`: action dataclasses, dispatch map, uniform tool-result formatting, result truncation (~200 lines head+tail with an elision marker)
- [x] 2.4 Implement read-only tools in `tools/files.py` / `tools/search.py`: `read_file` (numbered lines, offset/limit, 2k-line cap), `grep` (ripgrep subprocess wrapper + pure-Python fallback), `glob`/`list_dir` (pathspec gitignore filtering, depth cap)
- [x] 2.5 Implement `tools/shell.py`: `bash` via subprocess — cwd locked to project root, configurable timeout (default 60s), merged stdout/stderr, exit code in result, kill on timeout, reject interactive commands (heuristic: no TTY)
- [x] 2.6 `[HAND]` Implement `core/loop.py`: the master while-loop per `context.md` §4.1 — step cap, interrupt flag checked between steps, duplicate-action detection (identical tool+args twice consecutively → inject nudge message)
- [x] 2.7 Write `prompts/system.md` v1: role, action protocol spec with 2–3 few-shot examples per tool, behavioral rules ("read before edit", "verify with tests"); keep static portion <2,000 tokens
- [x] 2.8 Tool-trace UI: render each tool call as a compact colored line (tool, args, duration, result size), collapsible verbose mode via `/verbose`

**Exit criteria:** in the fixture repo, "which function handles login, and do the tests pass?" → agent greps, reads the file, runs pytest, answers correctly, loop terminates on its own. Parser corpus tests green.

---

## Phase 3 — Edit (write path + retry harness) — milestone M3, make-or-break

- [x] 3.1 `[HAND]` Implement the SEARCH-block matching cascade in `tools/edit.py`: exact → whitespace-normalized → fuzzy (difflib ratio >0.9, sliding window); reject ambiguous matches (≥2 candidates above threshold)
- [x] 3.2 Implement failure feedback: on no-match, tool result includes closest-match snippet + line number + similarity % + instruction to re-read and retry; retry cap 2–3 per edit tracked in loop state
- [x] 3.3 Implement whole-file fallback: auto-trigger for files <150 lines or after retry-cap exhaustion; system-prompt addendum instructing full-file fenced output; applier validates the result is plausibly complete (non-empty, no elision markers)
- [x] 3.4 Implement `write_file` (new files only — refuse existing paths, direct to edit_file) with parent-dir creation
- [x] 3.5 Unified diff generation + rich rendering (green/red) for every proposed change, shown before apply
- [x] 3.6 Instrument edit telemetry from day one: per-session counters for edit attempts, cascade tier used, retries, fallbacks, failures — dumped on `/stats` and at session end
- [x] 3.7 `tests/test_edit.py`: cascade tiers, ambiguity rejection, fuzzy boundaries, whole-file validation, retry accounting

**Exit criteria:** across 10 varied real edit tasks in a real repo ("rename this param and update callers", "add a CLI flag", "fix this failing test"), ≥8 land without manual file surgery. If not met, iterate on prompt + cascade here — do NOT proceed on a broken edit path.

---

## Phase 4 — Trust (permission gate + sandboxing) — milestone M4 ✓ complete

- [x] 4.1 Implement `permissions/gate.py` verdict engine: auto-allow read-only; ask for edit/write with diff preview `[y/n/a(lways for file)]`; ask for bash with `[y/n/a(lways for command)]` keyed on first token
- [x] 4.2 Hard-deny layer (model-unoverridable): realpath containment check against project root (catches `../` and symlinks); command regex blocklist per `context.md` §4.6; denials are logged and reported to the model as tool errors
- [x] 4.3 Session allowlist (in-memory, resets on exit) + persistent allowlist in `.arescode.toml`; `/allow` and `/deny` slash commands to inspect/edit
- [x] 4.4 Ensure gate reads ONLY parsed action fields, never model prose (prompt-injection containment); add a test with a fixture file containing hostile instructions and assert gate behavior is unchanged
- [x] 4.5 `--yolo` flag for auto-approve mode (explicitly opt-in, prints a warning banner)
- [x] 4.6 `tests/test_gate.py`: path escapes, symlink escape, blocklist hits, allowlist scoping

**Exit criteria:** agent cannot touch anything outside project root or run blocklisted commands even when explicitly instructed to in a prompt; approval UX feels fast (single keystroke). ✓ verified — `test_gate.py` proves escapes/blocklist are denied through the real `Executor`; approval is a single keystroke (`ui/approve.py`).

> **Implementation notes.** The interactive gate runs **inline in `core/loop.py`** (`_permit`, per context.md §4.1): allow / ask-and-approve / deny happens *before* the tool-execution timer starts, so a denial or the user's thinking time never counts as tool latency. Denials become failed `ToolResult`s the loop feeds back to the model as tool errors. The `Executor` shares the same `Gate` as a **belt-and-suspenders hard-deny backstop** (`Executor._check_permission` refuses a blocklisted command / path escape regardless of caller; ASK/ALLOW pass through). ASK verdicts are answered by an injected `Approver` (`ui/approve.py`: `interactive_approver` single-keystroke `y/n/a`, or `auto_approver` for `--yolo`). The approval preview is a **true unified diff** computed by a read-only dry-run (`edit.py:preview_edit` / `preview_write`) — the make-or-break `apply_edit` cascade is untouched. Persistent allowlists load from `.arescode.toml` (`allow_commands` / `allow_paths`); `/allow` and `/deny` edit the in-memory session allowlist. `loop.py` and `edit.py` are `[HAND]` files; these additions were made by Claude Code under the author's explicit authorization.

---

## Phase 5 — Endure (context management + memory) — milestone M5

- [ ] 5.1 Implement `repo/repomap.py`: gitignore-filtered file tree with sizes, capped ~1,500 tokens (breadth-first truncation for huge repos), injected into system prompt at session start; `/map` command to view
- [ ] 5.2 Implement ARES.md support: load from project root into system prompt if present; `arescode init` generates a starter template (project conventions, key commands)
- [ ] 5.3 Implement token accounting in `core/context.py`: `len//4` estimate per message, running total, budget = num_ctx minus reply reserve (~1,500 tokens)
- [ ] 5.4 Implement compaction: at 75% budget, summarize oldest turns into one assistant message via a dedicated summarization call; never compact system prompt, current task statement, or last 4 tool results; `/compact` forces it manually; show a subtle indicator when it fires
- [ ] 5.5 Long-run hardening: verify a 30+ step session on the 3050 stays coherent and within VRAM; tune default num_ctx if KV cache spills

**Exit criteria:** a long multi-task session (≥30 loop steps) completes without context overflow errors, incoherence from truncation, or VRAM thrash; resumed sessions include compacted history correctly.

---

## Phase 6 — Polish & ship (packaging + DX) — milestone M6

- [ ] 6.1 Complete slash commands: `/stats`, `/compact`, `/map`, `/allow`, `/deny`, `/verbose`, `/resume <id>`, `/sessions`
- [ ] 6.2 First-run experience: detect missing Ollama/model and print exact fix commands; graceful message when ripgrep absent
- [ ] 6.3 Error-path sweep: model server down mid-turn, malformed config, unreadable files, git-less directories — all produce clear messages, never tracebacks
- [ ] 6.4 Packaging: installable via `pipx install .`, console script `arescode`; version command; README with 5-minute quickstart (per docs principle: <5 min to first success)
- [ ] 6.5 Dogfood gauntlet: use AresCode itself (not Claude Code) for 3 real tasks on one of your other projects (e.g., a JARVIS or Suture fix); log every failure into `LATER.md` or the parser corpus
- [ ] 6.6 Write `LATER.md`: deferred features backlog (sub-agents, MCP, TODO planner, tree-sitter symbols, steering queue, multi-model routing) so scope creep has a home that isn't the codebase

**Exit criteria — MVP Definition of Done (from context.md §7):** in a real repo, `arescode` takes "fix the failing test in X", finds the file, edits it, reruns tests, and reports success — every write/command approved through the gate — using only the local model.

---

## Working agreement (read before every phase)

1. **Sequence is strict.** Phases build on each other; a broken Phase 3 makes Phases 4–6 meaningless.
2. **`[HAND]` tasks are non-negotiable** (D10): `parser.py`, `loop.py`, and the edit cascade get written by hand first. Claude Code may review them and write their tests, not author them.
3. **Every phase adds tests before its exit check** — the parser/edit corpus is the project's most valuable asset.
4. **Collect failures.** Every weird model output goes into `tests/fixtures/model_outputs/`. Every harness improvement should trace to a real recorded failure.
5. **Measure, don't vibe.** Edit success rate (3.6 telemetry) is the project's north-star metric; check it after every prompt or cascade change.
