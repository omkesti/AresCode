"""Master while-loop: context assembly -> model call -> parse -> gate -> execute -> repeat.

Single-threaded, one flat message history, hard step cap, interrupt flag checked between
steps, duplicate-action detection (context.md §4.1).

[HAND] Author by hand per decision D10 (TASKS 2.6). Claude Code may review this module and
write its tests, but must not author its logic.
"""
