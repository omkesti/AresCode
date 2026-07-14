"""Lenient parser: extract tool actions and SEARCH/REPLACE edit blocks from raw completions.

Tolerates marker-length drift, stray whitespace, missing fence languages, misplaced
filenames (hunt +/-3 lines), and multiple actions per response (context.md §4.2-4.3).

[HAND] Author by hand per decision D10 (TASKS 2.1). Claude Code may review this module and
write its tests, but must not author its logic.
"""
