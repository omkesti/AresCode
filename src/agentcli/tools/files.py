"""File tools: read_file (numbered lines, offset/limit, ~2k-line cap) and write_file.

write_file is new-files-only — it refuses to overwrite an existing path and directs the
model to edit_file instead (context.md §4.4, TASKS 2.4 / 3.4).
"""
