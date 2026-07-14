"""Terminal rendering with rich: streaming markdown, syntax highlighting, diffs, tool traces.

Streams tokens live then re-renders the final message as formatted markdown; colored unified
diffs for proposed edits; compact per-tool-call trace lines (context.md §3, TASKS 1.4 / 2.8).
"""
