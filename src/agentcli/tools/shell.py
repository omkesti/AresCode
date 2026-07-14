"""Sandboxed bash tool: subprocess with cwd locked to the project root.

Configurable timeout (default 60s, kill on expiry), merged stdout/stderr with exit code,
output truncation, and a no-TTY guard that rejects interactive commands (context.md §4.4,
TASKS 2.5).
"""
