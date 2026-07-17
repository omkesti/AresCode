# Model-output corpus

The home for **real malformed completions** collected from the local model during development and
dogfooding (`context.md` §4.3, CLAUDE.md, working agreement 4). This is the project's most valuable
asset: `parser.py` and `edit.py` are hardened against exactly these samples, not against imagined
failure modes.

## When to add a sample

Whenever the model produces output the parser/edit cascade mishandles (a drifted `<<<<<<` marker, a
misplaced filename, a nested fence, a SEARCH block that won't match) — before you fix the harness,
**save the raw output here** and add a case to `tests/test_parser.py` or `tests/test_edit.py` that
reproduces the miss. Then make the harness absorb it. Every harness improvement should trace to a
real recorded failure.

## Conventions

- One completion per file, verbatim (do not "clean it up" — the mess is the point).
- Name by symptom: `drifted-search-marker.txt`, `filename-below-block.txt`, `nested-fence.txt`.
- This directory is excluded from pytest collection (`--ignore=tests/fixtures` in pyproject), so
  files here are fixtures loaded by tests, never collected as tests themselves.

_(empty until the dogfood gauntlet — TASKS 6.5 / docs/DOGFOOD.md — starts turning up real misses)_
