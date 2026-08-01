"""FocusProbe protocol — one event shape, swappable backends (ADR-001)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

FocusSource = Literal["atspi", "x11", "wlr", "kde", "noop"]

FOCUS_SOURCES: frozenset[str] = frozenset({"atspi", "x11", "wlr", "kde", "noop"})


@dataclass
class FocusWindow:
    app: str
    title: str
    active: bool
    role: str = ""
    pid: int | None = None
    title_raw: str | None = None
    agent: dict[str, Any] | None = None


@runtime_checkable
class FocusProbe(Protocol):
    """Backend that can report the active window (and optional inventory)."""

    # str (not FocusSource) so fakes/tests are assignable (invariance of Literals)
    source: str

    def probe(self) -> bool:
        """True if this backend is usable right now."""
        ...

    def get_active(self) -> list[FocusWindow]:
        """Windows for a focus tick (active marked); may be empty."""
        ...

    def get_desktop(self) -> list[FocusWindow]:
        """Full inventory when available; default same as get_active."""
        ...


def raw_dicts_to_windows(rows: list[dict[str, Any]]) -> list[FocusWindow]:
    out: list[FocusWindow] = []
    for item in rows:
        pid_raw = item.get("pid")
        if isinstance(pid_raw, int):
            pid: int | None = pid_raw
        elif isinstance(pid_raw, str) and pid_raw.isdigit():
            pid = int(pid_raw)
        else:
            pid = None
        out.append(
            FocusWindow(
                app=str(item.get("app") or "unknown"),
                title=str(item.get("title") or ""),
                active=bool(item.get("active")),
                role=str(item.get("role") or ""),
                pid=pid,
            )
        )
    return out
