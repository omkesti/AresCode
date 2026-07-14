"""SEARCH/REPLACE edit applier: exact -> whitespace-normalized -> fuzzy matching cascade.

Rejects ambiguous matches, feeds closest-match failures back to the model, retries 2-3 times,
then falls back to whole-file mode (context.md §4.3, TASKS 3.1-3.3).

[HAND] Author the matching cascade by hand per decision D10 (TASKS 3.1). Claude Code may
review this module and write its tests, but must not author its logic.
"""
