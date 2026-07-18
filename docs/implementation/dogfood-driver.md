# Dogfood Driver — `scripts/dogfood.py`

> A push-button, headless version of the [`DOGFOOD.md`](../DOGFOOD.md) gauntlet. One command
> drives the **real** agent loop over the live local model across three coding tasks and reports
> edit-success — the `/stats` north-star check (working agreement 5), re-runnable any time the
> harness or prompt changes.
>
> This document is 50% *what it is / how to use it* and 50% *how it was implemented* — the wiring,
> the decisions, and the gotchas. For the manual walkthrough of what each task exercises, see
> `DOGFOOD.md`; for the architecture it sits on top of, see [`context.md`](../context.md) §3–4.

---

## Part 1 — What it is and how to use it

### 1.1 Why it exists

The pytest suite deliberately **never touches a live model** — the provider is exercised with an
`httpx.MockTransport`. That keeps CI fast and deterministic, but it means the thing the project is
actually about — *can a weak local model complete real edits through this harness?* — is never
measured by `pytest`. `DOGFOOD.md` filled that gap manually: launch AresCode inside a scratch
project, type three tasks, eyeball the diffs, run `/stats`.

The manual gauntlet has two problems: it's slow to repeat, and it's easy to skip after a harness
change. The edit-success rate is the project's north-star metric (working agreement 5), so it needs
to be **cheap to re-measure**. `scripts/dogfood.py` turns the manual ritual into one command.

It lives in `scripts/` (next to `check_ollama.py`), **not** in `tests/`, for a reason: it requires a
running Ollama and a pulled model, so it must never be collected by `pytest`. Being a plain script
keeps it out of the suite while staying a first-class, version-controlled artifact (the previous
throwaway `scratchpad/dogfood.py` vanished with the scratchpad — this replaces it with a persistent
home).

### 1.2 What it does

For each of three tasks it:

1. **Materializes a deterministic buggy scratch repo** in a fresh temp dir.
2. **Drives the real pipeline** — `OpenAICompatProvider` → `run_turn` → `parser` → `Gate` →
   `Executor` — with the `--yolo` `auto_approver`, so the run is fully non-interactive.
3. **Applies a task-specific pass/fail check** (run the code / run pytest / inspect structure).
4. **Prints the `DOGFOOD.md` log template plus `EditStats.summary()`** (`/stats`), then an overall
   PASS/FAIL line.
5. On any **parse/edit miss**, dumps the raw model completions to
   `tests/fixtures/model_outputs/` as a parser-corpus candidate (working agreement 4).

The three tasks mirror the Phase 3 edit gauntlet and the MVP Definition of Done (`context.md` §7):

| # | `tid` | Task | Harness surface it stresses |
|---|---|---|---|
| 1 | `fix-test` | Fix a failing test (buggy `add` returns `a - b`) | grep/read nav → `edit_file` cascade → `bash` (pytest) → loop terminates on green. The proven MVP-DoD smoke check. |
| 2 | `add-feature` | Add a `cube` function + call it from another file | Multi-file edits, write/edit gate, repo-map freshness after a change |
| 3 | `rename-param` | Rename a param and update its keyword callers | Fuzzy SEARCH/REPLACE tiers, retry-with-feedback, whole-file fallback |

### 1.3 Running it

On a box with a GPU and `ollama serve` running:

```powershell
python scripts/dogfood.py                                   # all three, 7B default
python scripts/dogfood.py --tasks 1 --verbose               # smoke task only, full tool trace
python scripts/dogfood.py --model qwen2.5-coder:14b-instruct --ctx 8192   # re-baseline the 14B
python scripts/dogfood.py --keep                            # keep passing scratch repos too
```

| Flag | Default | Effect |
|---|---|---|
| `--model` | `qwen2.5-coder:7b` | Ollama tag to drive |
| `--ctx` | `16384` | `num_ctx` for the run |
| `--base-url` | `http://localhost:11434/v1` | OpenAI-compatible endpoint (the `Config` default) |
| `--tasks` | all | Comma-separated subset, e.g. `1,3` |
| `--verbose` | off | Show full tool output in the trace |
| `--keep` | off | Keep scratch repos even when a task passes |

**Exit codes:** `0` = every selected task passed · `1` = at least one failed · `2` = preflight
failed (server down / model missing). This makes the driver usable as a gate in a shell one-liner.

Results are **printed, not appended** to `LATER.md` — you curate which findings land there, per the
`DOGFOOD.md` logging rule.

### 1.4 What a run looks like

```
Preflight (Ollama):
PASS  model 'qwen2.5-coder:7b' is available
PASS  chat completion succeeded with num_ctx=16384: 'ok'
OK - all checks passed

▶ fix-test  fix a failing test (buggy add returns a - b)
● bash  python -m pytest -q
  ! exit 1 | 6L | 812ms
● read_file  ops.py
  ok read_file | 2L | 3ms
● edit_file  ops.py
  ok exact | 4L | 5ms
● bash  python -m pytest -q
  ok exit 0 | 3L | 640ms

### 2026-07-18 — dogfood-scratch — task 1: fix a failing test (buggy add returns a - b)
- Model: qwen2.5-coder:7b   num_ctx: 16384
- Outcome: landed clean  (autonomous finish: True; verdict: pytest exit 0: 1 passed in 0.01s)
- /stats: edits: 1 attempted, 1 applied, 0 failed | tiers: exact=1 whitespace=0 fuzzy=0 whole_file=0
- Failure(s): —
- Raw model output (if a parse/edit miss): (none)

======================================================================
OVERALL: 3/3 tasks passed  ✅
  [PASS] task 1 (fix-test): landed clean
  [PASS] task 2 (add-feature): landed after retries
  [PASS] task 3 (rename-param): landed clean
======================================================================
```

---

## Part 2 — How it was implemented

### 2.1 Module layout

The file reads top-to-bottom as: **imports + path setup → task definitions (`Task`, verdicts,
`TASKS`) → the per-task runner (`run_one`, `TaskReport`) → reporting (`_print_report`, `_sweep`) →
CLI (`main`)**. The design goal was that the whole thing is *orchestration only* — it imports the
production modules and wires them exactly as the REPL does, adding nothing to the harness itself.
That matters for the `[HAND]` rule (D10): the driver never reimplements loop/parser/edit logic, so
building it required no changes to those files.

### 2.2 The import bootstrap

Two non-obvious import needs are handled up front (`dogfood.py:34–53`):

```python
_REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (_REPO_ROOT / "src", _REPO_ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from check_ollama import run_checks   # noqa: E402
from arescode.config import Config     # noqa: E402
```

- `src/` on `sys.path` lets the script run even without an editable install (`pip install -e` also
  works — the guard makes the path insert idempotent so there's no harm either way).
- `scripts/` on `sys.path` lets `import check_ollama` find its sibling regardless of the cwd the
  script is launched from.

The `arescode` and `check_ollama` imports therefore sit *below* executable code, so they carry
`# noqa: E402` (module-level-import-not-at-top). This is the one place the file deviates from
import hygiene, and it's deliberate and localized.

### 2.3 Wiring the real pipeline (`run_one`)

The core requirement — "drive the *real* loop" — is met by assembling the exact same objects
`ui/repl.py::run` builds, then handing them to the production `run_turn`:

```python
console        = render.make_console()
provider       = OpenAICompatProvider.from_config(config)
gate           = Gate.from_config(workdir, config)
executor       = Executor(workdir, config, gate=gate)      # gate = hard-deny backstop
approver       = auto_approver(console)                    # --yolo: approve every ASK
repo_map       = build_repo_map(workdir)
system_prompt  = assemble_system_prompt(workdir, repo_map=repo_map)
state          = SessionState.new(config.model)
observer       = render.ConsoleObserver(console, verbose=verbose)

final_text = await run_turn(
    task.prompt, state=state, provider=provider, executor=executor,
    system_prompt=system_prompt, observer=observer, max_steps=config.max_steps,
    gate=gate, approver=approver, num_ctx=config.num_ctx,
)
```

A few implementation choices worth calling out:

- **The scratch temp dir is the `project_dir`** for both `Gate` and `Executor`. The gate's path-escape
  hard-deny is therefore scoped to the scratch repo — a model that tries to write outside it is
  blocked exactly as in a real session.
- **`auto_approver` = `--yolo` semantics.** The gate still hard-denies path escapes and blocklisted
  commands (those bite in `_permit` / `Executor._check_permission` before the approver is consulted);
  the approver only rubber-stamps the ASK verdicts (writes/edits/shell). This is the same auto-approve
  path the interactive `--yolo` flag uses, so what's measured is the real gate, not a bypass.
- **`num_ctx=config.num_ctx` is passed through** so the compaction hook in `run_turn` runs for real.
  The scratch tasks are tiny and won't cross the budget, but wiring it keeps the path faithful rather
  than special-casing headless mode.
- **`render.make_console()`**, not a bare `Console()` — see §2.7.

One faithful-but-imperfect edge: the REPL rebuilds the system prompt/repo map *between* turns when the
tree changes; a single `run_turn` call does not. Since the driver runs one turn per task, task 2's
newly-created file isn't reflected in the in-context repo map mid-turn. That's acceptable — the model
has `glob`/`read` to discover it — and it matches how `run_turn` behaves for any single turn.

### 2.4 Tasks and verdicts (data-driven)

Each task is a frozen-ish `Task` dataclass carrying its `prompt`, its scratch-repo `files`
(`{relpath: content}`), and a `verdict` callable. `_materialize` writes the files; adding a fourth
task is just appending a `Task` to the `TASKS` list plus one verdict function.

The verdicts are the part that earns the "measure, don't vibe" rule. They return `(passed: bool,
detail: str)` and are built to be **execution-based wherever possible**, because grepping the model's
output for a symbol is easy to fool:

- **`_verdict_fix_test`** — runs `python -m pytest -q` in the scratch dir; pass ⇔ exit 0.
- **`_verdict_add_feature`** — runs `python app.py`; pass ⇔ exit 0 **and** both `25` and `125` appear
  in stdout. Running the program catches a missing import that a structural grep would miss (if the
  model adds `cube(5)` but forgets to import `cube`, the program raises and the task correctly fails).
- **`_verdict_rename`** — runs `python main.py` (which raises `TypeError` if a caller still passes the
  old keyword) **and** confirms `def greet(person` replaced `def greet(name` and no `name=` survives.

All verdicts shell out via a single `_run` helper that uses `sys.executable` (so pytest and the
interpreter match the venv), captures output, and never raises (`check=False`, `timeout=120`).

> **Verified offline:** for all three tasks the verdict *fails* the buggy original and *passes* a
> hand-written correct fix — proving the checks have neither false positives nor false negatives
> before a single model token is spent. This is the one piece of the driver that can be fully tested
> without a GPU, and it was.

### 2.5 Preflight — reusing `check_ollama.py`

Rather than duplicate the endpoint ping, `check_ollama.py` was refactored to expose a reusable
`run_checks(base_url, model, ctx) -> int`; its `main()` is now a thin argparse wrapper that calls it,
so the standalone CLI is unchanged (backward-compatible; the full suite still passes). `dogfood.py`
calls `run_checks` before touching the loop:

```python
if run_checks(_ollama_root(args.base_url), args.model, args.ctx) != 0:
    print("\nPreflight failed — fix the above and re-run.")
    return 2
```

`run_checks` prints the exact `ollama serve` / `ollama pull` fix on failure, so the driver doesn't
re-derive those messages. The small impedance mismatch is that `Config.base_url` carries a `/v1`
suffix while `check_ollama` wants the server root; `_ollama_root` strips a trailing `/v1` to bridge
the two (unit-checked for the `/v1`, `/v1/`, and no-suffix cases).

### 2.6 Failure capture → parser corpus

Working agreement 4 says every weird model output becomes a corpus case. After each task, if the
verdict failed **or** `executor.stats.failures > 0`, `_dump_completions` writes every assistant
message from the turn to `tests/fixtures/model_outputs/dogfood-<tid>-<date>.txt`:

```python
completions = [m.content for m in state.messages if m.role == "assistant"]
```

The raw completions are recovered from `SessionState` rather than a bespoke capture hook — the loop
already appends every model completion to history via `state.assistant(text)`, so walking
`state.messages` for `role == "assistant"` yields exactly the text the parser saw. The dump lands in
the fixtures directory (which is `--ignore`d by pytest, so it never perturbs the suite) with a header
telling the reader to curate it into a real corpus case and then delete the dump. Detecting a "parse
miss" precisely is fuzzy, so the driver uses the task verdict as the proxy: a parse miss ⇒ no working
edit ⇒ failed verdict ⇒ dump. That over-captures slightly (a dump on any failure), which is the safe
direction for a corpus-collection tool.

### 2.7 UTF-8 output safety (tie-in to the Finding B fix)

The driver is meant to be redirected to a log (`python scripts/dogfood.py > run.txt`), and it prints
non-ASCII glyphs — `▶` per task, `—` in the log template, `✅`/`❌` in the summary — both directly via
`print()` and through the rich trace. On Windows a redirected stdout defaults to cp1252, which cannot
encode those glyphs; unguarded, the run would die with `UnicodeEncodeError` — the **exact class of bug
logged as dogfood Finding B** and fixed for the REPL.

That fix is reused here. `render.make_console()` protects the rich trace, and `main()` additionally
calls `render.force_utf8(sys.stdout)` before its first `print` so the driver's own report lines are
safe too:

```python
# main(), before any output:
render.force_utf8(sys.stdout)   # reconfigure to UTF-8, errors="replace" — same guard as Finding B
```

> **Verified offline:** the full report template renders through a simulated cp1252 pipe without
> raising, with the em-dash and emoji intact.

### 2.8 Reporting and control flow

`run_one` returns a `TaskReport` (verdict, `EditStats` summary, a `used_fallback_tier` flag derived
from `stats.fuzzy or stats.whole_file`, the dump path, and whether the loop finished autonomously).
`_outcome` maps that to the `DOGFOOD.md` vocabulary — `landed clean` / `landed after retries` /
`failed` — and `_print_report` fills in the template. Autonomous-finish detection is a string check
against the two fixed sentences `run_turn` returns when it hits the step cap or the stall guard
(`"Reached the step limit"` / `"kept repeating"`); anything else means the model chose to stop.

The whole sweep runs under a single `asyncio.run(_sweep(...))` (`run_turn` is async; the provider
opens its own `AsyncClient` per call). Preflight runs synchronously first — it's a plain `httpx` ping
and there's no reason to enter the event loop only to bail. Scratch dirs are removed on pass (unless
`--keep`) and **kept on failure** with the path printed, so a failing run leaves the exact repo state
behind for inspection.

---

## 3. What is and isn't covered without a GPU

| Aspect | How it was checked |
|---|---|
| Imports resolve, `--help` works | Run directly |
| Verdict correctness (no false pos/neg) | Materialize each task; assert buggy → FAIL, correct fix → PASS |
| Preflight-failure path | Point at an unreachable port; assert exit 2 + fix hint |
| `_ollama_root`, `_select_tasks` | Unit assertions incl. range/format errors |
| Report renders on cp1252 pipe | Drive `_print_report` through a guarded `TextIOWrapper` |
| **The live sweep itself** | **Requires a GPU + Ollama — run on-device by the author** |

Everything that does not need a live model is verified; the one thing that does — the actual
model-in-the-loop sweep — is exactly what the driver exists to make a single command.

---

## 4. Files touched

| File | Change |
|---|---|
| `scripts/dogfood.py` | **New.** The driver described here. |
| `scripts/check_ollama.py` | Extracted `run_checks(base_url, model, ctx)` from `main()` for reuse; CLI behavior unchanged. |
| `docs/DOGFOOD.md` | Added a "Push-button sweep" section pointing at the driver; manual walkthrough retained as the source of truth for *what* each task exercises. |

No `[HAND]` files (`core/loop.py`, `core/parser.py`, `tools/edit.py`) were modified — the driver is
pure orchestration around the existing loop.
