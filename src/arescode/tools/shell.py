"""Sandboxed bash tool: subprocess with cwd locked to the project root.

Configurable timeout (kill on expiry), merged stdout/stderr with the exit code, stdin closed
so interactive programs fail fast instead of hanging (context.md §4.4, TASKS 2.5).

A minimal denylist blocks obviously catastrophic or interactive commands. This is a Phase-2
stopgap; the real permission gate + blocklist arrive in Phase 4 (context.md §4.6).
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from arescode.tools.base import ToolError

# Catastrophic or interactive commands we refuse even before the Phase-4 gate exists.
_BLOCKED = [
    r"rm\s+-rf\s+[/~]",
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r":\(\)\s*\{",  # fork bomb
    r"\b(shutdown|reboot|halt|poweroff)\b",
    r">\s*/dev/sd",
]
_INTERACTIVE = [
    r"^\s*(vim?|nano|emacs|less|more|top|htop|man|ssh|ftp|telnet)\b",
    r"^\s*(python3?|node|irb|psql|mysql)\s*$",  # bare REPLs
]


@dataclass(slots=True)
class BashResult:
    exit_code: int
    output: str  # merged stdout + stderr
    timed_out: bool = False


def run_bash(cmd: str, project_dir: Path, *, timeout: float = 60.0) -> BashResult:
    """Run ``cmd`` with cwd locked to ``project_dir`` and stdin closed."""
    stripped = cmd.strip()
    if not stripped:
        raise ToolError("empty command")
    for pat in _BLOCKED:
        if re.search(pat, stripped):
            raise ToolError(f"blocked command (matches safety rule /{pat}/)")
    for pat in _INTERACTIVE:
        if re.search(pat, stripped):
            raise ToolError("interactive commands are not supported (no TTY)")

    try:
        proc = subprocess.run(
            stripped,
            shell=True,
            cwd=str(project_dir),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        partial = (exc.output or "") + (exc.stderr or "")
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", "replace")
        partial += f"\n[killed: exceeded {timeout:.0f}s timeout]"
        return BashResult(exit_code=124, output=partial, timed_out=True)

    output = (proc.stdout or "") + (proc.stderr or "")
    return BashResult(exit_code=proc.returncode, output=output.strip())
