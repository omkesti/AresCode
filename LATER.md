# LATER.md — Deferred scope

> The home for everything deliberately **out** of the MVP. When a good idea shows up mid-build,
> it goes here — not into the codebase (`context.md` §1, §9 "scope creep"; TASKS 6.6).
> The MVP contract is in `docs/context.md` §1. Nothing below ships until the MVP Definition of Done
> (context.md §7) is met and dogfooded.
>
> Each item records **what**, **why it was deferred**, and any **prerequisite** so a future pickup
> starts from intent rather than re-derivation.

---

## Post-MVP feature backlog

Explicitly deferred in `context.md` §1 / §8. Kept out because each one either dilutes a small
model's instruction-following or adds infrastructure the MVP doesn't need to prove its thesis.

### Agent capability
- **Sub-agents / multi-agent orchestration.** Parallel or delegated agents with their own histories.
  *Deferred:* D1 — the single-threaded flat-history loop is the project's soul; branching hides the
  learning and multiplies failure modes on a weak model. *Prereq:* a rock-solid single loop first.
- **TODO / plan-first planner.** An explicit plan step that decomposes a task into tracked subtasks.
  *Deferred:* weak models plan poorly and the loop already terminates on plain text; measure whether
  navigation actually fails before adding planning ceremony.
- **Steering queue (mid-task instruction injection).** A proper async input queue so the user can
  add instructions while a turn runs — the full version of today's between-steps interrupt flag
  (`core/loop.py`, context.md §4.1). *Prereq:* the interrupt flag is the MVP stand-in; upgrade only
  when the single-turn UX is otherwise solid.
- **Automatic multi-model routing.** Per-task model selection (cheap model for search, strong model
  for edits). *Deferred:* D12 — only the **manual** `/model` switch is in scope; auto-routing needs a
  reliable task classifier and per-model telemetry first.

### Tools & context
- **More tools beyond the six.** Every added tool measurably dilutes a small model's
  instruction-following (D6). New tools must clear that bar with evidence, not convenience.
- **Tree-sitter symbol maps.** Aider-style top-level symbols per file in the repo map, replacing the
  size-only tree (`repo/repomap.py`, context.md §4.5). *Prereq:* `tree-sitter` +
  `tree-sitter-languages` deps; only worth it once grep/read navigation demonstrably strains.
- **Embeddings / RAG retrieval.** Vector search over the codebase. *Deferred:* D7 — Claude Code
  navigates with grep/read and so do we; revisit only if navigation demonstrably fails. Avoids a
  vector DB and an indexing pipeline.
- **MCP (Model Context Protocol) support.** External tool/server integration. *Deferred:* large
  surface, orthogonal to proving the local-model loop.

### Integration & extensibility
- **IDE integration.** VS Code / JetBrains surfaces. *Deferred:* the terminal REPL is the MVP.
- **Hooks.** User-defined pre/post-action shell hooks. *Deferred:* post-MVP extensibility.
- **Custom / user-defined slash commands.** MVP ships a fixed command set (TASKS 6.1).

### Safety hardening
- **System-prompt prompt-injection hardening.** The gate already ignores model prose and reads only
  parsed action fields (context.md §4.6), so tool-result injection can't change gate behavior. What's
  deferred is hardening the *system prompt* itself against adversarial tool output. *Prereq:* the
  containment guarantee stays the MVP's security floor; this is defense-in-depth on top.

---

## Known risks being carried (from `context.md` §9)

Not features — open risks tracked so they don't get forgotten. Move a mitigation here into a real
task if the risk bites during dogfooding.

- **Edit-reliability floor.** Even with the full cascade, a local model may fail multi-file or
  long-range edits. Mitigations in place: whole-file fallback, small-diff prompting, telemetry.
  Watch the `/stats` edit-success rate — it is the north-star metric (TASKS 5, working agreement 5).
- **Stale edit-success baseline (post-D11).** Every recorded edit-success number — including Phase 3's
  ≥8/10 gauntlet — was measured on `qwen2.5-coder:7b` and is **unverified** on
  `qwen2.5-coder:14b-instruct`. Re-run the Phase 3 10-task gauntlet once on the 14B to re-baseline;
  `scripts/dogfood.py --model qwen2.5-coder:14b-instruct --ctx 8192` gives a fast 3-task subset of
  that signal (and prints `/stats` per task) as a starting point. Caveat: on the RTX 3050 the 14b is
  a coin-flip (see the VRAM-ceiling note below), so a run may need retries and a crashed load is
  inconclusive, not a re-baseline. Counters are not reset in code; `/stats` doesn't know which model
  produced them.
- **VRAM ceiling (<12GB GPUs).** The ≈9GB Q4 14B spills to CPU on a sub-12GB GPU and crawls; the
  ≈4.7GB 7B default fits. Mitigations: 7B default (D13), a smaller per-model `num_ctx` for the 14B
  (8192), aggressive compaction. Confirmed constraint on the author's RTX 3050 6GB — the 14B is a
  VRAM-pressure-dependent coin-flip there (loads sometimes, fails CUDA init others), so the 7B is the
  only *reliable* GPU-resident option on that box.
- **Loop pathologies.** Small models re-read the same file forever or declare victory early.
  Mitigations: step cap + duplicate-action nudge. Feed any new pathology into the loop as a nudge,
  not a new subsystem.

---

## Dogfood findings (TASKS 6.5)

Log every failure from running AresCode on a real project here (or, when it's a malformed model
output, into `tests/fixtures/model_outputs/` as a new parser-corpus case). Each entry: the task,
what went wrong, and whether the fix belongs in the harness, the prompt, or this backlog.

### 2026-07-17 — headless gauntlet, task 1: "fix the failing test"

Ran the real agent loop (provider → `run_turn` → parser → gate → executor, `--yolo` auto-approver)
against live Ollama on a scratch project with a bugged `add` (`return a - b`). Driver:
`scratchpad/dogfood.py`.

- **`qwen2.5-coder:14b-instruct`: PASS end-to-end.** pytest (fail) → `read_file` → `edit_file` that
  landed on the **exact** cascade tier → pytest (2 passed) → done. `/stats`: `1 attempted, 1 applied,
  0 failed, exact=1`. **This meets the MVP Definition of Done on a live local model.**
- **`qwen2.5-coder:7b`: initially FAILED, now PASSES after the parser fix below.** The harness
  worked (bash, read, the unsaved-change nudge all fired), but the model emitted the SEARCH/REPLACE
  block with **bare `SEARCH` / `REPLACE` keywords and no `<<<<<<< ======= >>>>>>>` conflict
  markers**. `SR_RE` needed those markers and `_parse_tool` had no `edit_file` branch, so nothing
  parsed → treated as a plain-text answer → turn ended with `edits: none`. With Finding A fixed, a
  re-run landed the edit on the **exact** tier and both tests passed (`/stats`: `1 applied, exact=1`).

**Finding A — parser gap (harness) — FIXED (2026-07-17).** Bare-marker SEARCH/REPLACE is a real
weak-model output. `parser.py` now rescues it: when a `<tool>edit_file</tool>` tag is present and no
conflict markers are found, `_bare_edit_block` splits on bare `SEARCH`/`REPLACE` lines (tolerating
markdown bold / a trailing colon / code fences), **scoped to the edit-tag window** so ordinary prose
that mentions SEARCH/REPLACE is never misread (guarded by a false-positive test). Recorded:
`tests/fixtures/model_outputs/bare-search-replace-no-markers.txt` + `tests/test_parser_dogfood.py`
(now passing, incl. the fenced variant, the scoping guard, and a conflict-marker regression).
`parser.py` is a `[HAND]` file (D10) — this change was made under the author's explicit
authorization and is now covered by `tests/test_parser_dogfood.py`, so the verification flag is
cleared (2026-07-18).

**Finding B — trace renderer crashes on non-UTF-8 stdout (Windows) — FIXED (2026-07-18).**
`render.tool_start` prints `●` (U+25CF); when stdout is a *redirected* cp1252 pipe rather than a
UTF-8 terminal (as in a headless dogfood run), rich's write raised `UnicodeEncodeError` and took
down the turn. The interactive REPL on Windows Terminal (a real UTF-8 tty) was always unaffected.
Fix: `render.make_console()` (used by `repl.run`) now forces UTF-8 stdout via `render.force_utf8`
(`sys.stdout.reconfigure(encoding="utf-8", errors="replace")`, guarded for streams without
`reconfigure`) before constructing the `Console`, so UTF-8 pipes get the real glyph and any
residual is `?`-replaced rather than fatal. Headless regression coverage added in
`tests/test_render.py` (a cp1252 pipe crashes without the guard, survives with it).

**Observation — 14B on the RTX 3050 6GB (reconciled 2026-07-18).** The 14b loaded and ran two full
turns this session (GPU reported free). This is *consistent* with — not contra — the recorded
behavior: on this box the 14b is a **VRAM-pressure-dependent coin-flip** — it loads sometimes and
fails CUDA init other times, even at identical `num_ctx` (full detail in the project memory
`gpu-vram-6gb-14b-crashes`). A clean run is a load-success sample, not evidence it always loads, so
there is no contradiction left to resolve. What remains open is only the **D11 re-baseline** (the
stale-baseline risk above), which needs a *successful* 14b load to mean anything — a crashed load is
inconclusive, not a data point.
