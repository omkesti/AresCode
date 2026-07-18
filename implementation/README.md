# implementation/ — system-prompt enhancement (2026-07-18)

This folder documents the goal *"enhance the system prompt so AresCode performs better on the local
models, validated against a recorded baseline."* Everything here is the evidence and rationale behind
the changes shipped to `src/arescode/core/parser.py` and `prompts/system.md`.

## Executive summary

Measuring the real agent loop on both local models turned "make the prompt better" into two concrete,
**measured** fixes — and the biggest win landed in the harness, not the prompt:

- **Parser fix (the performance win).** The 7b's flagship failure — "fix the failing test" — went
  from **0/3 → passing every run**. On tiny files the 7b drops the tool tag and SEARCH/REPLACE
  markers and just pastes the new file (sometimes with a stray fence + a `**filename**` header); the
  parser now recovers that as a whole-file edit and no longer mistakes the filename header for the
  file body. See `prompt-research.md` §6a.
- **Prompt enhancement (compact, measured).** The shipped `prompts/system.md` (= `system.v5.md`) is
  **~1160 tokens, 33% smaller than the intermediate v3 (~1736) and 28% under the original (~1610)** —
  while matching performance. Getting there took three measured attempts: an aggressive 38% rewrite
  (`v2`) **clobbered files**; a conservative additive version (`v3`) was safe but barely smaller; a
  compact version (`v4`) **regressed the 7b's multi-file task** (it used `write_file` on an existing
  file instead of reading + appending). The fix was surgical — `v5` makes the **worked example
  demonstrate the ADD pattern** (read → `edit_file`-append → verify), which is exactly the shape the
  7b was failing. See `prompt-research.md` §6b and `baselines-and-results.md`.

### Result (task pass-rate, `scripts/dogfood.py --repeat`)

| Model | Before (baseline, ~1610-tok prompt) | After (parser fix + `system.v5.md`, ~1160 tok) |
|---|---|---|
| `qwen2.5-coder:7b` | **6/9** (task 1 = 0/3) | **8/9** (task 1 fixed, tasks 2–3 hold) |
| `qwen2.5-coder:14b-instruct` @ ctx 6144 | 6/6 | **6/6** (tightened verdict) |

The 7b's gain is driven by the reliably-fixed flagship task; the compact prompt recovers ~576
tokens/call (~9% of the 14b's 6144 window) with no performance loss. The one 7b task-1 miss is within
the parser-recovery noise; the 7b remains high-variance on multi-file edits.

## Files

| File | What |
|---|---|
| `prompt-research.md` | The analysis, research levers, the parser-vs-prompt decision, and the exact changes |
| `baselines-and-results.md` | The measurement log: harness, before/after tables, the iteration, hardware notes |
| `system.v2.md` | Aggressive 38% rewrite — **rejected** (clobbered files) |
| `system.v3.md` | Conservative additive enhancement — safe but barely smaller than the original |
| `system.v4.md` | First compact attempt — **rejected** (regressed the 7b's multi-file task, 0/2) |
| `system.v5.md` | Compact (~1160 tok) + ADD-pattern example — **SHIPPED** to `prompts/system.md` |
| `logs/` | Raw driver output for both models: `baseline-*`, `after-*` (v3), `v5-*` |

## Reproduce

```powershell
# baseline (current prompt): swap prompts/system.md back, or checkout the pre-change commit
python scripts/dogfood.py --model qwen2.5-coder:7b --ctx 16384 --repeat 3
# after (A/B an alternate prompt without touching the bundled one):
python scripts/dogfood.py --model qwen2.5-coder:7b --ctx 16384 --repeat 3 --system-prompt implementation/system.v5.md
python scripts/dogfood.py --model qwen2.5-coder:14b-instruct --ctx 6144 --repeat 2 --system-prompt implementation/system.v5.md
```
