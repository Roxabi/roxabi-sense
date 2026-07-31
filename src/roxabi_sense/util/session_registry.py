"""Load Grok/Claude session registries with mtime+size signature cache.

Avoids re-reading JSON on every focus tick when files are unchanged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_GROK_SESSIONS = Path.home() / ".grok" / "active_sessions.json"
_CLAUDE_SESSIONS_DIR = Path.home() / ".claude" / "sessions"


@dataclass
class _FileSig:
    mtime_ns: int = -1
    size: int = -1

    def matches(self, path: Path) -> bool:
        try:
            st = path.stat()
        except OSError:
            return self.mtime_ns == -1 and self.size == -1
        return st.st_mtime_ns == self.mtime_ns and st.st_size == self.size

    def capture(self, path: Path) -> None:
        try:
            st = path.stat()
            self.mtime_ns = st.st_mtime_ns
            self.size = st.st_size
        except OSError:
            self.mtime_ns = -1
            self.size = -1


@dataclass
class SessionRegistry:
    """Cached session lists; re-read only when signature changes."""

    grok_path: Path = field(default_factory=lambda: _GROK_SESSIONS)
    claude_dir: Path = field(default_factory=lambda: _CLAUDE_SESSIONS_DIR)
    _grok_sig: _FileSig = field(default_factory=_FileSig)
    _grok_data: list[dict[str, Any]] = field(default_factory=list)
    _claude_sig: tuple[Any, ...] = ()
    _claude_data: list[dict[str, Any]] = field(default_factory=list)

    def load_all(self) -> list[dict[str, Any]]:
        return [*self.load_grok(), *self.load_claude()]

    def load_grok(self) -> list[dict[str, Any]]:
        if self._grok_sig.matches(self.grok_path):
            return self._grok_data
        self._grok_sig.capture(self.grok_path)
        self._grok_data = _parse_grok_file(self.grok_path)
        return self._grok_data

    def load_claude(self) -> list[dict[str, Any]]:
        sig = _claude_dir_sig(self.claude_dir)
        if sig == self._claude_sig:
            return self._claude_data
        self._claude_sig = sig
        self._claude_data = _parse_claude_dir(self.claude_dir)
        return self._claude_data


# Module-level default (shared across collectors / focus enrich)
_default_registry = SessionRegistry()


def default_registry() -> SessionRegistry:
    return _default_registry


def load_grok_sessions(path: Path | None = None) -> list[dict[str, Any]]:
    if path is not None:
        return _parse_grok_file(path)
    return _default_registry.load_grok()


def load_claude_sessions(path: Path | None = None) -> list[dict[str, Any]]:
    if path is not None:
        return _parse_claude_dir(path)
    return _default_registry.load_claude()


def load_all_sessions() -> list[dict[str, Any]]:
    return _default_registry.load_all()


def _parse_grok_file(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "agent": "grok",
                "session_id": item.get("session_id"),
                "pid": item.get("pid"),
                "cwd": item.get("cwd"),
                "opened_at": item.get("opened_at"),
                "source": str(path),
            }
        )
    return out


def _claude_dir_sig(path: Path) -> tuple[Any, ...]:
    if not path.is_dir():
        return ()
    rows: list[tuple[str, int, int]] = []
    try:
        for f in path.glob("*.json"):
            try:
                st = f.stat()
            except OSError:
                continue
            rows.append((f.name, st.st_mtime_ns, st.st_size))
    except OSError:
        return ()
    return tuple(sorted(rows))


def _parse_claude_dir(path: Path) -> list[dict[str, Any]]:
    """Claude Code registry: ~/.claude/sessions/<pid>.json (sessionId+pid+cwd)."""
    if not path.is_dir():
        return []
    out: list[dict[str, Any]] = []
    try:
        files = sorted(path.glob("*.json"))
    except OSError:
        return []
    for f in files:
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        sid = raw.get("sessionId") or raw.get("session_id")
        pid = raw.get("pid")
        cwd = raw.get("cwd")
        if sid is None and pid is None:
            continue
        out.append(
            {
                "agent": "claude",
                "session_id": sid,
                "pid": pid,
                "cwd": cwd,
                "opened_at": raw.get("startedAt") or raw.get("opened_at"),
                "name": raw.get("name"),
                "entrypoint": raw.get("entrypoint"),
                "source": str(f),
            }
        )
    return out


def file_signature(path: Path) -> tuple[int, int] | None:
    """(mtime_ns, size) or None if missing — for collectors / tests."""
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)
