"""Tool registry: action dataclasses, the dispatch map, and uniform result formatting.

Truncates results to ~200 lines (head + tail with an elision marker) before they re-enter
the message history (context.md §4.4, TASKS 2.3).
"""
