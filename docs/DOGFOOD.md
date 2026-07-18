# DOGFOOD.md — the Phase 6.5 gauntlet

> The MVP is "done" only when AresCode can do real work **on a real repo, driven by the local
> model** — not just pass tests (which never touch a live model). This is the on-device check the
> author runs; it cannot be run in CI or by an assistant without a GPU + Ollama.
>
> Rule (working agreement 4–5): **every failure gets logged** — a malformed model output becomes a
> new parser-corpus case (`tests/fixtures/model_outputs/`), a missing feature or rough edge goes to
> [`LATER.md`](../LATER.md), and an edit-reliability regression is a note against the `/stats`
> north-star metric. Measure, don't vibe.

## Push-button sweep (`scripts/dogfood.py`)

The three tasks below are also wired into a headless driver so edit-success can be re-measured with
one command whenever the harness or prompt changes (the `/stats` north-star check, working
agreement 5). It preflights Ollama, materializes a deterministic buggy scratch repo per task, drives
the **real** pipeline (provider → `run_turn` → parser → gate → executor) with the `--yolo`
auto-approver, applies a task-specific pass/fail check, and prints the log template + `/stats` per
task. A parse/edit miss dumps the raw model completions to `tests/fixtures/model_outputs/` as a
parser-corpus candidate (agreement 4). Results are **printed, not auto-appended** — curate what lands
in this file.

```powershell
ollama serve                                        # in one terminal
python scripts/dogfood.py                            # all three tasks on the 7B default
python scripts/dogfood.py --tasks 1 --verbose        # just the smoke task, full tool trace
python scripts/dogfood.py --repeat 3                  # each task x3 -> a pass RATE, not a noisy 1/1
python scripts/dogfood.py --system-prompt PATH        # A/B an alternate base prompt (prompt/parser work)
python scripts/dogfood.py --model qwen2.5-coder:14b-instruct --ctx 6144   # 14B (6144 = the 6GB ceiling; 8192 500s)
```

Exit code is 0 only if every selected task passes. **`--repeat N`** reports a per-task pass rate — the
metric of record, since raw `/stats` undercounts unparsed edits (it counts an unrecognized edit as
`0 attempted`). **`--system-prompt PATH`** swaps the base prompt so a prompt change can be measured
before it ships. Note: **background runs hit a duration limit mid-sweep — run foreground, and split
per-task for the slow 14B**. The 2026-07-18 baseline for both models (7B 6/9→8/9 after the parser
fix, 14B 6/6) is written up in [`implementation/`](../implementation/). The manual walkthrough below
is still the source of truth for *what* each task exercises and how to log a finding.

## Before you start

```powershell
ollama serve                       # in one terminal
arescode --version                 # confirm the install
```

Launch inside a **different** project (a JARVIS/Suture fix, per TASKS 6.5) — dogfooding on AresCode
itself is too incestuous. On startup AresCode now runs a preflight (TASKS 6.2): if the server is
down or the model isn't pulled, it prints the exact `ollama serve` / `ollama pull` fix.

## The three tasks

Pick three genuine, independent tasks. Suggested shapes (mirroring the Phase 3 edit gauntlet and the
MVP Definition of Done in `context.md` §7):

1. **Fix a failing test.** "The test in `X` fails — find it, fix the code, rerun the tests, confirm
   green." Exercises: grep/read navigation → `edit_file` cascade → `bash` (pytest) → the loop
   terminating on success.
2. **Add a small feature across a couple of files.** e.g. "Add a `--json` flag to command `Y`."
   Exercises: multi-file edits, the write/edit approval gate, repo-map freshness after a new file.
3. **A refactor or rename with callers.** "Rename param `a` to `b` and update its callers."
   Exercises: the fuzzy SEARCH/REPLACE tiers, retry-with-feedback, whole-file fallback.

For each: does the agent finish autonomously? Do the diffs apply cleanly through the gate? Run
`/stats` at the end and record the edit-success tiers.

## Logging template (append results to LATER.md § "Dogfood findings")

```
### <date> — <project> — task N: <one-line task>
- Model: <tag>   num_ctx: <n>
- Outcome: landed clean / landed after retries / manual surgery / failed
- /stats: attempts=.. exact=.. whitespace=.. fuzzy=.. whole_file=.. failures=..
- Failure(s): <what went wrong> -> destination: parser corpus | LATER.md | prompt tweak
- Raw model output (if a parse/edit miss): saved to tests/fixtures/model_outputs/<name>.txt
```

## Exit criterion (MVP Definition of Done)

At least the "fix the failing test" task must complete end-to-end — find the file, edit it, rerun
the tests, report success — with every write/command approved through the gate, using only the local
model. If it can't, the fix belongs in the harness (parser/cascade/prompt), not in lowering the bar.
The D11 edit-success re-baseline was run on 2026-07-18 (7B 8/9, 14B 6/6 at `num_ctx = 6144`; see
`implementation/` and `context.md` §9); a broader multi-task sweep is the remaining open item.
