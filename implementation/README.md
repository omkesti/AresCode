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
- **Prompt enhancement (conservative, measured).** `prompts/system.md` gains the highest-confidence
  research levers — the critical "text changes nothing; use a tool" rule at **primacy and recency**,
  and negation→positive phrasing — with the proven body intact. An aggressive 38% rewrite was tried
  first and **measurement caught it regressing** (whole-file clobbering); the shipped version is
  additive. See `prompt-research.md` §6b and the "iteration" section of `baselines-and-results.md`.

### Result (task pass-rate, `scripts/dogfood.py --repeat`)

| Model | Before (baseline) | After (parser fix + `system.v3.md`) |
|---|---|---|
| `qwen2.5-coder:7b` | **6/9** (task 1 = 0/3) | **6/6** (task 1 fixed) |
| `qwen2.5-coder:14b-instruct` @ ctx 6144 | 6/6 | **6/6** (now under a tightened verdict) |

The 7b's gain is driven by the reliably-fixed flagship task; tasks 2–3 hold (the 7b is high-variance
on multi-file edits, but the v2 clobbering regression is gone).

## Files

| File | What |
|---|---|
| `prompt-research.md` | The analysis, research levers, the parser-vs-prompt decision, and the exact changes |
| `baselines-and-results.md` | The measurement log: harness, before/after tables, the iteration, hardware notes |
| `system.v2.md` | The **rejected** aggressive rewrite (kept as the record of what measurement caught) |
| `system.v3.md` | The **shipped** conservative enhancement (promoted to `prompts/system.md`) |
| `logs/` | Raw driver output: `baseline-7b.txt`, `baseline-14b.txt`, `after-7b.txt`, `after-14b.txt` |

## Reproduce

```powershell
# baseline (current prompt): swap prompts/system.md back, or checkout the pre-change commit
python scripts/dogfood.py --model qwen2.5-coder:7b --ctx 16384 --repeat 3
# after (A/B an alternate prompt without touching the bundled one):
python scripts/dogfood.py --model qwen2.5-coder:7b --ctx 16384 --repeat 3 --system-prompt implementation/system.v3.md
python scripts/dogfood.py --model qwen2.5-coder:14b-instruct --ctx 6144 --repeat 2 --system-prompt implementation/system.v3.md
```
