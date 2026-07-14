"""Shared tool primitives: the error type raised by tool implementations.

Kept dependency-free so the individual tool modules (files, search, shell) can import it
without creating a cycle back through the registry.
"""

from __future__ import annotations


class ToolError(Exception):
    """An expected, user-facing tool failure (missing file, bad regex, timeout, blocked command).

    The executor turns this into a failed ``ToolResult`` rather than letting it crash the loop.
    """
