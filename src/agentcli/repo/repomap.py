"""Repo map: a gitignore-filtered file tree with sizes, capped ~1,500 tokens.

Injected into the system prompt at session start; breadth-first truncation for large repos.
tree-sitter top-level symbols are a post-MVP addition (context.md §4.5, TASKS 5.1).
"""
