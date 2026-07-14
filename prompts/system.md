<!--
  prompts/system.md — the versioned system prompt (never hardcoded in Python).

  Authored in Phase 2 (TASKS 2.7). It will hold: the role definition, the action-protocol
  spec with 2-3 few-shot examples per tool, the SEARCH/REPLACE rules, the "read before edit"
  and "verify with tests" behavioral rules, and injection points for the repo map + ARES.md.

  Keep the static portion under ~2,000 tokens — every token here is paid on every model call
  (context.md §4.8).
-->
