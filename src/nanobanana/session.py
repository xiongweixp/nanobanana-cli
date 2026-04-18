"""Runtime session manager — no persistence, lives in process memory."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class SessionManager:
    """
    Stores active Gemini Chat instances keyed by session name.

    Rules (from design doc):
    - create(): if name already exists, silently replace the old one.
    - get(): returns None when session not found (caller decides what to do).
    - delete(): no-op when session is not found.
    """

    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def create(self, name: str, chat: Any, cwd: str = ".") -> bool:
        """Create (or replace) a session.  Returns True if a previous session was replaced."""
        replaced = name in self._store
        self._store[name] = {
            "chat": chat,
            "cwd": cwd,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        return replaced

    def get(self, name: str) -> Any | None:
        """Return the raw Gemini chat object, or None if session does not exist."""
        entry = self._store.get(name)
        return entry["chat"] if entry else None

    def get_cwd(self, name: str) -> str:
        """Return the working directory for a session."""
        entry = self._store.get(name)
        return entry["cwd"] if entry else "."

    def delete(self, name: str) -> bool:
        """Delete a session.  Returns True if it existed."""
        if name in self._store:
            del self._store[name]
            return True
        return False

    def list(self) -> list[dict[str, str]]:
        """Return [{name, created_at}, ...] for all active sessions."""
        return [
            {"name": name, "created_at": info["created_at"]}
            for name, info in self._store.items()
        ]
