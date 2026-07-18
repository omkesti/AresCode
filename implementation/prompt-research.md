# System-prompt enhancement — research, decision, and changes

> Deliverable for the 2026-07-18 goal: enhance the system prompt so AresCode performs measurably
> better on the local target models (`qwen2.5-coder:7b`, `qwen2.5-coder:14b-instruct`), with the
> change validated against a recorded baseline. This doc is the *why* and *what*;
> [`baselines-and-results.md`](baselines-and-results.md) is the *measured evidence*.

## 1. The constraint (unchanged)

The target model is far weaker than a frontier model, so reliability must live in the **harness**,
not in the model's instruction-following. The litmus test for every change: *"would this still work
if the model gets it 80% right?"* This governs both changes below — and it is why the biggest fix
turned out to belong in the **parser**, not the prompt.

## 2. The prompt as it was (measured)

| Property | Value | Consequence |
|---|---|---|
| Size | **6,439 chars ≈ 1,610 tokens** (`len/4`) | ~80% of the ~2,000-token budget **before** ARES.md + repo map |
| On 14b @ `num_ctx=6144` | prompt + repo map ≈ 40%+ of the window | Weak models degrade as the window fills |
| Example block | 34 lines / ~350 tokens, heavily defensive | ~22% of the prompt spent telling the model *not* to perform the example |
| `write_file`/edit-vs-write guidance | repeated ~4× with varied wording | Redundant; drift reads as inconsistency to a weak model |
| Critical "act with a tool" rule | line 90 of 144 (the middle) | The worst position for attention (see §3) |
| Negations (`never`/`do not`/`not`) | ~10+ | Weak models process negation poorly |

## 3. Research levers (why weak models specifically)

1. **Lost in the middle** (Liu et al. 2023): models attend most to the **start and end**;
   middle content is under-used — and the effect *worsens* as models shrink. The single most
   important behavioral rule sat dead-center.
2. **Negation is processed poorly** by smaller models — "never use bash to write files" still
   activates *bash + write*. Positive imperatives are more reliable.
3. **Worked examples beat abstract rules** for weak models (the basis of Aider's design), but each
   example costs tokens and risks the model *performing* it.
4. **Context frugality aids coherence** — a leaner always-on prompt leaves more room to stay
   coherent, especially on the 14b's smaller 6144 window.

## 4. What the dogfood baseline actually revealed

Running the real loop on both models (3 tasks, repeated — see `baselines-and-results.md`) turned
"make the prompt better" into two concrete, measured failures:

- **Finding C (7b, the flagship failure): tiny-file edits are dropped.** On a 2-line file the 7b
  writes the fix as a bare markdown block with a **bold filename and no tool tag and no
  SEARCH/REPLACE markers** — matching none of the parser's recognized forms, so the edit is silently
  dropped (`/stats: 0 attempted`) and the task fails **0/3**. This is a *parser* gap, not a
  prompt-wording problem — the "fix the harness, not the prompt" case in its purest form.
- **Finding D (14b): SEARCH-block imprecision.** The 14b passes, but its first-try SEARCH blocks
  frequently no-match and only land via the whole-file/fuzzy fallback (once corrupting an edit with a
  duplicated `main()`). A *prompt* lever (steer toward small, exact SEARCH blocks) plus the existing
  cascade.

## 5. Decision: split the work by where reliability belongs

- **Parser owns Finding C.** Instructing a weak model out of its most natural markdown shape is the
  low-percentage bet; making the harness *accept* that shape is robust. (Same philosophy as the
  earlier Finding A bare-marker recovery.)
- **Prompt owns the rest**: length, positioning, negation, and steering the 14b toward exact SEARCH
  blocks — and it can now *lean on* the parser (and the loop's unsaved-file nudge) instead of
  restating enforcement prose.

## 6. Changes made

### 6a. Parser (`core/parser.py`) — Finding C recovery `[HAND]`, minimal, flagged

Two additions (authorized under the relaxed-D10 memory; kept tight, covered by tests):
1. **Tag-less recovery** (`_recover_whole_file_edit`, `_FILE_HEADER_RE`): a `filename` line
   (optionally `**bold**`, with an extension) immediately above a fenced block → an **empty-SEARCH
   whole-file edit**. Called **only when nothing else parsed**, so it can never override a real tool
   call or misread an incidental code block.
2. **Tagged whole-file fallback** in the `edit_file` branch: `<tool>edit_file</tool>` + a fenced
   block with no SEARCH/REPLACE markers → whole-file edit, scoped to the tag window.

Both emit empty-SEARCH edits that `apply_edit` still validates (file must exist, no elision markers,
Python syntax must hold) before writing, and both pass through the permission gate. Corpus fixture
`tests/fixtures/model_outputs/tagless-whole-file-codeblock.txt` + 4 tests in
`tests/test_parser_dogfood.py` (recovery, the no-double-fire guard, the tagged variant, and a
no-false-positive case).

### 6b. System prompt (`prompts/system.md` ← `implementation/system.v2.md`)

Applied the research; **38% shorter (1,610 → ~1,001 tokens, 144 → 111 lines)**:

| Change | Lever | Type |
|---|---|---|
| Critical "text changes nothing; act with a tool" rule moved to **primacy (line 2) and recency (last line)** | Lost-in-middle | test |
| Collapsed the ~4× `write_file`/edit-vs-write guidance to one positive statement | Frugality / negation | safe |
| Negations → positive imperatives ("Change files only through edit_file/write_file") | Negation | safe |
| Shrank the example to ~10 lines with an **un-performable** name (`demo_widget.py`, never in the repo map) and dropped most anti-perform scaffolding | Few-shot / frugality | test |
| **Canonicalized `edit_file` to the tagged form** (`<tool>edit_file</tool><path>…</path>` + SEARCH/REPLACE **or** a full-file fence) — consistent with the other five tools, and both forms verified against the parser | Format entropy | test |
| Added explicit "small SEARCH blocks / exact copy" guidance | Finding D (14b) | test |
| Whole-file edit path now matches what the hardened parser accepts | Aligns prompt with harness | — |

The taught formats were verified offline to parse to the correct actions before any model time was
spent (targeted edit, whole-file edit, write_file, read_file, bash).

## 7. How "better" is measured

Task **pass-rate** via `scripts/dogfood.py --repeat N` on **both** models against the recorded
baseline — not raw `/stats`, which the baseline proved misleading (blind to the 7b's unparsed edits;
pessimistic for the 14b's fallback landings). The prompt draft is A/B'd via `--system-prompt` before
being promoted to `prompts/system.md`. One variable is not perfectly isolated here (parser + prompt
change together), but the parser fix targets a failure the prompt cannot, and the end-to-end
before/after is the goal's success criterion. See `baselines-and-results.md`.
