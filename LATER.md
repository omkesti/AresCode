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
  long-range edits. Mitigations in place: whole-file fallback, small-diff prompting, telemetry. Edit
  success is the north-star metric, but the 2026-07-18 sweep showed raw `/stats` is a misleading
  *headline* (blind to unparsed edits — they count as `0 attempted`); read it **alongside** task
  pass-rate from `scripts/dogfood.py --repeat N` (TASKS 5, working agreement 5).
- **Edit-success baseline (post-D11) — MEASURED 2026-07-18 (see Dogfood findings below).** A 3-task ×
  repeat sweep now exists for both models (7b: **6/9** task pass-rate; 14b: **6/6**). The 14b run is
  at `--ctx 6144`, not 8192 — 8192 500s on the RTX 3050 (VRAM-ceiling note below). What's still not
  apples-to-apples: Phase 3's original ≥8/10 was a 10-task *edit-tier* number on 7b, versus the new
  3-task *pass-rate* metric — widen the task set before calling it a like-for-like re-baseline.
  `/stats` counters are not reset in code and don't know which model produced them.
- **VRAM ceiling (<12GB GPUs) — quantified 2026-07-18.** The ≈9GB Q4 14B spills to CPU on a sub-12GB
  GPU and crawls; the ≈4.7GB 7B default fits. On the author's RTX 3050 6GB the 14b's limit is the
  **KV-cache size set by `num_ctx`**: it reliably **serves at `num_ctx ≤ 6144`** and reliably **500s
  at 8192** (measured across the baseline sweep plus direct probes at 2048/4096/6144/8192) — a
  deterministic threshold, not a random coin-flip. So **D12's recommended 8192 for the 14b is too
  high for this box; use ≤6144 there.** Mitigations: 7B default (D13), a per-model `num_ctx` of 6144
  (not 8192) for the 14b on a 6GB GPU, aggressive compaction.
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

**Observation — 14B on the RTX 3050 6GB (quantified 2026-07-18, supersedes the earlier "coin-flip").**
The 14b's load behavior is **not random** — it is a deterministic `num_ctx`/KV-cache threshold:
direct probes and a full 6-run baseline sweep show it serves reliably at `num_ctx ≤ 6144` and 500s at
8192. The earlier "loads sometimes, fails others" impression was almost certainly runs at/near the
8192 ceiling. Practical rule on this box: run the 14b at `--ctx 6144`. Project memory
`gpu-vram-6gb-14b-crashes` updated to match.

### 2026-07-18 — first full 3-task baseline sweep, both models (`scripts/dogfood.py --repeat`)

Established the pre-prompt-rewrite baseline: the headless gauntlet (all 3 tasks) on live Ollama, so
the system-prompt work has a real "before" to be measured against. Raw logs live in the scratchpad
(`baseline-7b.txt`, `baseline-14b.txt`). Driver enablers added for it — `--system-prompt PATH` (A/B
an alternate base prompt) and `--repeat N` (a pass RATE, not a noisy 1/1).

| Task | 7b (×3, ctx 16384) | 14b (×2, ctx 6144) |
|---|---|---|
| 1 fix-test | **0/3** | 2/2 exact |
| 2 add-feature | 3/3 exact | 2/2 (via whole-file/fuzzy fallback) |
| 3 rename-param | 3/3 exact | 2/2 exact |
| **Overall** | **6/9** | **6/6** |

**Finding C — 7b drops tag-less/marker-less whole-file edits on tiny files (harness gap) — FIXED (2026-07-18).**
On the 2-line `ops.py` the 7b writes the fix as a bare markdown block with a bold filename and *no*
tool tag and *no* SEARCH/REPLACE markers:

    **ops.py**
    ```python
    def add(a, b):
        return a + b
    ```

This matches none of the parser's three recognized edit forms (conflict-marker; bare-keyword =
Finding A; this), so the edit is silently dropped (`/stats: 0 attempted`), pytest keeps failing, and
the stall guard ends the turn — 0/3. The 14b avoids it (uses real SEARCH/REPLACE even on tiny files).
Per "fix the harness, not the prompt", the fix landed in the **parser** (`parser.py`, `[HAND]` under
the relaxed-D10 memory, minimal + tested): (1) a guarded tag-less recovery (`_recover_whole_file_edit`,
only when nothing else parsed) and (2) a tagged-`edit_file` whole-file fence fallback — plus a third
fix found while measuring: `_write_content` now skips a stray `**filename**` block so it isn't
mistaken for the file body (the actual blocker — it compiled to a syntax error and the edit was
rejected). Result: 7b task 1 went **0/3 → passing every run**; overall 7b **6/9 → 6/6**. Full write-up
in [`implementation/`](implementation/). Corpus + tests in `tests/test_parser_dogfood.py`.

**Finding D — 14b SEARCH blocks miss often, lean on the fallback (edit-quality, watch).** The 14b
passed every task, but its first-try SEARCH blocks frequently no-matched (task 2 run 1: `6 attempted,
2 applied, 4 failed`), landing only via the whole-file/fuzzy fallback — the cascade working as
designed. Once, a **fuzzy match corrupted the edit** (duplicated `main()` → `25 125 25 125`) and the
task-2 verdict false-passed it; the driver verdict is now tightened to require exactly `["25",
"125"]` (same commit as the enablers). Prompt lever: steer the 14b toward minimal, exact SEARCH
blocks.
