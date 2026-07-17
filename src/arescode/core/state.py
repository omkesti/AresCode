"""Session state: the flat message history plus JSON save/resume.

Roles: system / user / assistant / tool. Autosave to ``.arescode/sessions/<id>.json``;
``load_latest`` powers ``--resume`` (context.md §4.1, TASKS 1.5).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

SESSIONS_SUBDIR = Path(".arescode") / "sessions"
VALID_ROLES = frozenset({"system", "user", "assistant", "tool"})


def sessions_dir(project_dir: Path) -> Path:
    return project_dir / SESSIONS_SUBDIR


@dataclass(slots=True)
class SessionInfo:
    """A saved session's metadata, read without loading its full history (for ``/sessions``)."""

    session_id: str
    model: str
    created_at: str
    message_count: int
    path: Path


def _new_session_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass(slots=True)
class Message:
    role: str
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Message:
        return cls(role=str(data["role"]), content=str(data.get("content", "")))


@dataclass(slots=True)
class SessionState:
    """The flat conversation history for one session."""

    model: str
    session_id: str = field(default_factory=_new_session_id)
    created_at: str = field(default_factory=_now_iso)
    messages: list[Message] = field(default_factory=list)

    # --- mutation ---------------------------------------------------------
    def append(self, role: str, content: str) -> Message:
        if role not in VALID_ROLES:
            raise ValueError(f"unknown message role: {role!r}")
        message = Message(role=role, content=content)
        self.messages.append(message)
        return message

    def user(self, content: str) -> Message:
        return self.append("user", content)

    def assistant(self, content: str) -> Message:
        return self.append("assistant", content)

    def clear(self) -> None:
        """Drop all history, preserving a leading system message if present."""
        if self.messages and self.messages[0].role == "system":
            del self.messages[1:]
        else:
            self.messages.clear()

    # --- serialization ----------------------------------------------------
    def to_wire(self) -> list[dict[str, str]]:
        """Render the history as OpenAI-style role/content dicts for a provider."""
        return [m.to_dict() for m in self.messages]

    def _payload(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "model": self.model,
            "created_at": self.created_at,
            "messages": [m.to_dict() for m in self.messages],
        }

    def save(self, project_dir: Path) -> Path:
        directory = sessions_dir(project_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.session_id}.json"
        path.write_text(json.dumps(self._payload(), indent=2), encoding="utf-8")
        return path

    # --- construction -----------------------------------------------------
    @classmethod
    def new(cls, model: str) -> SessionState:
        return cls(model=model)

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> SessionState:
        state = cls(
            model=str(data.get("model", "")),
            session_id=str(data.get("session_id") or _new_session_id()),
            created_at=str(data.get("created_at") or _now_iso()),
        )
        state.messages = [Message.from_dict(m) for m in data.get("messages", [])]
        return state

    @classmethod
    def load(cls, path: Path) -> SessionState:
        return cls.from_payload(json.loads(path.read_text(encoding="utf-8")))

    @classmethod
    def load_latest(cls, project_dir: Path) -> SessionState | None:
        directory = sessions_dir(project_dir)
        files = sorted(directory.glob("*.json"), key=lambda p: (p.stat().st_mtime, p.name))
        if not files:
            return None
        return cls.load(files[-1])

    @classmethod
    def list_sessions(cls, project_dir: Path) -> list[SessionInfo]:
        """Metadata for every saved session, newest first — cheap enough for ``/sessions``.

        Session ids are ``%Y%m%d-%H%M%S`` timestamps, so a reverse id sort is chronological.
        Unreadable or malformed session files are skipped rather than crashing the listing.
        """
        directory = sessions_dir(project_dir)
        if not directory.is_dir():
            return []
        infos: list[SessionInfo] = []
        for path in directory.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError, ValueError):
                continue  # a truncated/half-written session must not break the whole listing
            infos.append(
                SessionInfo(
                    session_id=str(data.get("session_id") or path.stem),
                    model=str(data.get("model", "")),
                    created_at=str(data.get("created_at", "")),
                    message_count=len(data.get("messages") or []),
                    path=path,
                )
            )
        infos.sort(key=lambda i: i.session_id, reverse=True)
        return infos

    @classmethod
    def resolve(cls, project_dir: Path, ref: str) -> tuple[SessionState | None, str | None]:
        """Load a session by exact id or a unique id-prefix (for ``/resume <id>``).

        Returns ``(state, None)`` on success or ``(None, error_message)`` — an empty ref, no
        match, or an ambiguous prefix each yield a user-facing message the REPL can print.
        """
        ref = ref.strip()
        if not ref:
            return None, "usage: /resume <session-id>"
        infos = cls.list_sessions(project_dir)
        if not infos:
            return None, "no saved sessions to resume"
        exact = [i for i in infos if i.session_id == ref]
        matches = exact or [i for i in infos if i.session_id.startswith(ref)]
        if not matches:
            return None, f"no session matches '{ref}' (try /sessions to list them)"
        if len(matches) > 1:
            shown = ", ".join(i.session_id for i in matches[:5])
            more = "…" if len(matches) > 5 else ""
            reason = f"'{ref}' matches {len(matches)} sessions: {shown}{more} — be more specific"
            return None, reason
        return cls.load(matches[0].path), None
