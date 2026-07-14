"""Permission gate: allow / ask / deny verdicts with sandboxing.

Deny-first, per-action. Auto-allow read-only tools; ask (with a preview) for writes and shell;
hard-deny path escapes outside the project root and blocklisted commands. Hard denials are
**not overridable by the model** and never prompt the user.

The gate inspects ONLY parsed action fields (paths, command strings), never model prose — so
hostile text inside a file or a command's output can never change a verdict (prompt-injection
containment, context.md §4.6, TASKS 4.1-4.4).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:  # avoid a runtime import cycle (registry imports this module)
    from arescode.config import Config
    from arescode.tools.registry import Action


class Decision(Enum):
    ALLOW = "allow"  # run without asking (read-only, or an allowlisted write/command)
    ASK = "ask"  # prompt the user with a preview
    DENY = "deny"  # hard refusal, reported to the model as a tool error


@dataclass(frozen=True, slots=True)
class Verdict:
    decision: Decision
    reason: str = ""  # DENY: why (surfaced to the model)
    scope: str = ""  # ASK: "command" | "file" — what an "always" answer would remember
    key: str = ""  # ASK: the command token or file path to remember


class Approval(NamedTuple):
    approved: bool
    remember: bool = False


# A callable the UI supplies to answer an ASK verdict, given a precomputed change preview
# (a unified diff for writes/edits, "" for shell). See ui/approve.py.
Approver = Callable[["Action", Verdict, str], Approval]

_ALLOW = Verdict(Decision.ALLOW)

# grep/glob are confined to the project dir by their implementations, so they are always allowed.
READONLY_TOOLS = frozenset({"grep", "glob"})


def _build_blocklist() -> list[tuple[re.Pattern[str], str]]:
    """Hard-deny command patterns (context.md §4.6). Each entry: (pattern, human reason)."""

    def p(source: str) -> re.Pattern[str]:
        return re.compile(source, re.IGNORECASE)

    return [
        # Recursive/forced deletion of a root-ish target (/, ~, $HOME, *).
        (p(r"\brm\s+-[a-z]*[rf][a-z]*\s+(-[a-z]+\s+)*(/|~|\$HOME|\*)(\s|/|$)"),
         "recursive delete of / ~ $HOME or *"),
        (p(r"\bmkfs\b"), "filesystem format"),
        (p(r"\bdd\b[^\n]*\bof=/dev/"), "raw write to a device"),
        (p(r">\s*/dev/(sd|nvme|hd|disk)"), "raw write to a disk device"),
        (p(r":\s*\(\s*\)\s*\{.*\}\s*;\s*:"), "fork bomb"),
        (p(r"\b(shutdown|reboot|halt|poweroff)\b"), "power/state change"),
        (p(r"\bsudo\b"), "privilege escalation (sudo)"),
        (p(r"\bgit\s+push\b[^\n]*\s(--force\b|-f\b)"), "force push"),
        (p(r"\b(curl|wget|fetch)\b[^|\n]*\|\s*(sudo\s+)?(sh|bash|zsh|dash|python3?|node)\b"),
         "pipe-to-shell execution"),
        (p(r"(>>?|\btee\b|\bcp\b|\bmv\b|\bchmod\b|\binstall\b)[^\n]*\.ssh/"),
         "write to ~/.ssh"),
        (p(r"\b(curl|wget|nc|ncat|scp|ftp)\b[^\n]*\.env\b"), ".env exfiltration"),
        (p(r"\.env\b[^\n]*\|[^\n]*\b(curl|wget|nc|ncat)\b"), ".env exfiltration"),
    ]


DEFAULT_BLOCKLIST = _build_blocklist()


def _first_token(cmd: str) -> str:
    """The command a bash string ultimately invokes: skips VAR=val prefixes, strips any path."""
    for part in cmd.strip().split():
        if re.match(r"^\w+=", part):  # leading environment assignment, e.g. FOO=bar cmd
            continue
        return Path(part).name if ("/" in part or "\\" in part) else part
    return ""


@dataclass(slots=True)
class Gate:
    """Deny-first verdict engine for a single project root."""

    project_root: Path
    session_commands: set[str] = field(default_factory=set)  # in-memory, resets on exit
    session_paths: set[str] = field(default_factory=set)
    persistent_commands: frozenset[str] = frozenset()  # from .arescode.toml
    persistent_paths: frozenset[str] = frozenset()
    blocklist: list[tuple[re.Pattern[str], str]] = field(default_factory=lambda: DEFAULT_BLOCKLIST)

    def __post_init__(self) -> None:
        self.project_root = Path(self.project_root).resolve()

    @classmethod
    def from_config(cls, project_root: Path, config: Config) -> Gate:
        return cls(
            project_root=Path(project_root),
            persistent_commands=frozenset(config.allow_commands),
            persistent_paths=frozenset(config.allow_paths),
        )

    # --- verdicts ---------------------------------------------------------
    def check(self, action: Action) -> Verdict:
        """Return the permission verdict for one parsed action (never reads model prose)."""
        tool = getattr(action, "tool", "")
        if tool in READONLY_TOOLS:
            return _ALLOW
        if tool == "read_file":
            escape = self._containment(action.path)
            return escape or _ALLOW
        if tool in ("write_file", "edit_file"):
            escape = self._containment(action.path)
            if escape is not None:
                return escape
            if action.path in self.session_paths or action.path in self.persistent_paths:
                return _ALLOW
            return Verdict(Decision.ASK, scope="file", key=action.path)
        if tool == "bash":
            return self._bash_verdict(action.cmd)
        return Verdict(Decision.ASK)  # unknown tool: ask to be safe

    def _bash_verdict(self, cmd: str) -> Verdict:
        for pattern, reason in self.blocklist:
            if pattern.search(cmd):
                return Verdict(Decision.DENY, reason=f"blocked command ({reason})")
        token = _first_token(cmd)
        if token and (token in self.session_commands or token in self.persistent_commands):
            return _ALLOW
        return Verdict(Decision.ASK, scope="command", key=token)

    def _containment(self, path: str) -> Verdict | None:
        """A DENY verdict if ``path`` resolves outside the project root, else None."""
        if not self._within_root(path):
            return Verdict(Decision.DENY, reason=f"path escapes the project root: {path}")
        return None

    def _within_root(self, path: str) -> bool:
        # resolve() applies realpath, so this catches both ``../`` and symlink escapes.
        try:
            target = (self.project_root / path).resolve()
        except (OSError, ValueError, RuntimeError):
            return False
        return target == self.project_root or self.project_root in target.parents

    # --- allowlist management (session; TASKS 4.3) ------------------------
    def allow_always(self, verdict: Verdict) -> None:
        """Remember an 'always' answer from an approval prompt for the rest of the session."""
        if verdict.scope == "command" and verdict.key:
            self.session_commands.add(verdict.key)
        elif verdict.scope == "file" and verdict.key:
            self.session_paths.add(verdict.key)

    def allow_command(self, token: str) -> None:
        self.session_commands.add(token)

    def deny_command(self, token: str) -> bool:
        """Drop ``token`` from the session command allowlist; True if it was present."""
        if token in self.session_commands:
            self.session_commands.discard(token)
            return True
        return False

    def describe_allowlist(self) -> str:
        def show(label: str, items: frozenset[str] | set[str]) -> str:
            return f"{label}: {', '.join(sorted(items)) if items else '(none)'}"

        return "\n".join([
            show("session commands", self.session_commands),
            show("session paths", self.session_paths),
            show("config commands", self.persistent_commands),
            show("config paths", self.persistent_paths),
        ])
