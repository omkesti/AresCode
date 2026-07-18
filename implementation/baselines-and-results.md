# Baselines & results — dogfood measurement log

> All live-model tests run for the 2026-07-18 prompt-enhancement goal, on the author's machine
> (RTX 3050 6GB, Ollama 0.32.0). Raw driver output is preserved under [`logs/`](logs/). The *why* is
> in [`prompt-research.md`](prompt-research.md).

## Test harness

`scripts/dogfood.py` drives the **real** agent pipeline (provider → `run_turn` → parser → gate →
executor) against live Ollama with the `--yolo` auto-approver, over three deterministic scratch-repo
tasks, and applies a task-specific pass/fail verdict:

| # | Task | Exercises | Verdict |
|---|---|---|---|
| 1 | fix a failing test (`add` returns `a-b`) | grep/read → edit → pytest → loop terminates | `pytest` exit 0 |
| 2 | add `cube` across two files | multi-file edits, repo-map freshness | `def cube` present **and** `app.py` prints exactly `25` then `125` |
| 3 | rename param + update keyword callers | fuzzy SEARCH/REPLACE, retries, whole-file fallback | param renamed, callers updated, program runs |

Two driver enablers were added for this work: **`--repeat N`** (run each task N times → a pass
*rate*, not a noisy 1/1) and **`--system-prompt PATH`** (A/B an alternate base prompt). The task-2
verdict was also tightened to require exactly `["25","125"]` output, so a duplicated-`main()`
(a fuzzy-match mishap) can no longer false-pass.

**Metric of record:** task **pass-rate**. Raw `/stats` edit-tiers are reported too but are a
misleading headline — blind to the 7b's unparsed edits (they count as `0 attempted`) and pessimistic
for the 14b's fallback landings.

## Hardware note — 14b `num_ctx` ceiling (measured)

On the RTX 3050 6GB, `qwen2.5-coder:14b-instruct` (9 GB Q4) load is bounded by KV-cache size:
**serves reliably at `num_ctx ≤ 6144`, reliably 500s at 8192** (probed at 2048/4096/6144/8192 + a
full 6-run sweep). **D12's recommended 8192 is too high on this box** — the 14b is measured at 6144.
The 7b (4.7 GB) runs at the full 16384.

---

## BEFORE — baseline (current `prompts/system.md`, old parser)

Raw: [`logs/baseline-7b.txt`](logs/baseline-7b.txt), [`logs/baseline-14b.txt`](logs/baseline-14b.txt).

| Task | **7b** (×3, ctx 16384) | **14b** (×2, ctx 6144) |
|---|---|---|
| 1 — fix-test | **0/3** ❌ | 2/2 ✅ exact |
| 2 — add-feature | 3/3 ✅ | 2/2 ✅ (via fallback) |
| 3 — rename-param | 3/3 ✅ | 2/2 ✅ exact |
| **Overall** | **6/9** | **6/6** |

**7b:** the flagship task fails **0/3** — Finding C (tag-less/marker-less whole-file block on the
2-line file → edit dropped → stall). Tasks 2–3 land cleanly on the exact tier.
**14b:** passes all, but leans on the whole-file/fuzzy fallback on task 2 (run 1: `6 attempted, 2
applied, 4 failed`); one fuzzy match corrupted the edit with a duplicated `main()` that the *old*
verdict false-passed (the tightened verdict now rejects it).

> Note: the 14b baseline of 6/6 used the *old* task-2 verdict. Under the tightened verdict, the
> duplicated-`main()` run would be a FAIL — so the honest 14b "before" on today's ruler is closer to
> **5/6**. The AFTER runs below use the tightened verdict throughout.

---

## The iteration (two regressions caught by measurement)

The first prompt attempt was an aggressive 38% rewrite (`system.v2.md`). Measuring it against the
baseline caught **two** problems that "vibing" would have shipped:

1. **v2 over-promoted whole-file rewrites** → the 7b clobbered `mathutil.py` (replaced `square` with
   `cube`), breaking `app.py`'s import. Fix: `system.v3.md` makes SEARCH/REPLACE the sole taught edit
   form and restores the "to add code, keep the surrounding code / never drop code" guidance; the
   whole-file path is only a tool-guided fallback.
2. **The real task-1 blocker was a parser bug, not the prompt.** The 7b emits
   `<tool>edit_file</tool>` + a **stray empty fence** + a `**filename**` header + the code fence;
   `_fenced_blocks` captured `**ops.py**` as the file body → `apply_edit` compiled it → "line 2:
   invalid syntax" → edit rejected. Fix: `_write_content` now skips a block that is just a filename
   header (regression test added).

**Lesson (logged):** the research predicted "shorter is better," but on the 7b, cutting the prompt's
reinforcement *hurt* — the enhancement that works is **additive** (critical rule at primacy+recency,
positive phrasing) with the working body intact. Measurement overrode the prior.

## AFTER — fully-fixed parser + conservative enhanced prompt (`system.v3.md`)

Raw: [`logs/after-7b.txt`](logs/after-7b.txt), [`logs/after-14b.txt`](logs/after-14b.txt). Run with
`--system-prompt implementation/system.v3.md` against the parser with the Finding C recovery **and**
the filename-header extraction fix.

| Task | **7b** (×2, ctx 16384) | **14b** (×2, ctx 6144) |
|---|---|---|
| 1 — fix-test | **2/2** ✅ (was 0/3) — lands via `whole_file` recovery | **2/2** ✅ `exact` |
| 2 — add-feature | **2/2** ✅ correct `['25','125']`, no clobber | **2/2** ✅ (via fallback) |
| 3 — rename-param | **2/2** ✅ | **2/2** ✅ |
| **Overall** | **6/6** ✅ | **6/6** ✅ |

**7b notes.** Task 1 is now **reliably fixed** — the flagship failure (0/3 at baseline) lands every
run via the parser recovery. Task 2 is intrinsically **high-variance** on the 7b (across all runs
this session it ranged 1/3–3/3); the ×2 sweep here passed 2/2 with correct multi-file edits and no
clobbering — the v2 regression is gone. The dominant, reliable win is the task-1 parser fix.

**14b notes.** All tasks pass, **now under the tightened task-2 verdict** — the baseline's 6/6
included a fuzzy-corrupted `main()` that the old verdict false-passed, so 14b is *at least as good*
and arguably cleaner. Its SEARCH-block imprecision persists (task 2 lands via the whole-file/fuzzy
fallback), unchanged by this work — a prompt/harness item for later. (Background runs on the 14b hit
a duration limit and were completed foreground per-task; raw log notes this.)

## Before → after comparison

| | 7b | 14b @ ctx 6144 |
|---|---|---|
| **Before** | 6/9 — task 1 **0/3** | 6/6 (old verdict; 1 false-pass) |
| **After** | **6/6** — task 1 fixed | 6/6 (tightened verdict) |
| **Net** | task 1 0/3 → reliable; no regressions | maintained, stricter bar |

**Verdict: the system performs better.** The improvement is concentrated where it matters most — the
7b (the out-of-the-box default) now lands the MVP's flagship "fix the failing test" task, which it
failed every time at baseline. The 14b holds under a stricter check. The win is a **parser
robustness fix** (accept the tiny-file edit shape the 7b naturally produces); the prompt enhancement
is conservative and measured not to regress. Every number here is a task pass-rate from the real loop
on the live models, not a guess.
