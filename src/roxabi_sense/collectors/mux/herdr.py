"""Herdr live agent snapshot (structural fields + normalized session title).

``title`` is the agent session title (OMP auto-titles from the first prompt), with the
spinner / prompt chrome stripped so a spinner frame never re-emits a snapshot. Raw
terminal titles are never kept; coarse surfaces drop ``title`` (ADR-002).

Emitted on change, plus a keyframe every ``KEYFRAME_S`` while nothing changes, so
report-time interval math can tell "still working" from "daemon down / suspended".
``focused`` (server-focused pane, agent or plain shell) is present only when Herdr
answered; its absence means unknown, not "nothing focused".
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from roxabi_sense.store import Store
from roxabi_sense.util import mux
from roxabi_sense.util.titles import normalize_title, sanitize_display

KIND = "herdr_snapshot"
KEYFRAME_S = 300.0
_PANE_KEYS = ("pane_id", "cwd", "agent", "status", "focused", "session_id", "title")
_TITLE_MAX = 120


def _boot_clock() -> float:
    """Monotonic clock that keeps counting through suspend (first tick after resume
    re-emits); CLOCK_MONOTONIC would not, leaving the resume uncovered."""
    boottime = getattr(time, "CLOCK_BOOTTIME", None)
    return time.clock_gettime(boottime) if boottime is not None else time.monotonic()


class HerdrSessionsCollector:
    name = "herdr"

    def __init__(
        self,
        *,
        list_agents: Callable[[], list[dict[str, Any]]] | None = None,
        focused_pane: Callable[[], dict[str, str] | None] | None = None,
        clock: Callable[[], float] = _boot_clock,
    ) -> None:
        self._list_agents = list_agents
        self._focused_pane = focused_pane
        self._clock = clock
        self._last: str | None = None
        self._last_emit: float | None = None

    def tick(self, store: Store) -> int:
        if self._list_agents is None and not mux.herdr_bin():
            return 0
        agents = self._list_agents() if self._list_agents is not None else mux.list_herdr_agents()
        focused = (
            self._focused_pane() if self._focused_pane is not None else mux.herdr_focused_pane()
        )
        payload: dict[str, Any] = {
            "count": len(agents),
            "panes": [_snapshot_pane(a) for a in agents],
        }
        if focused is not None:
            payload["focused"] = focused
        fingerprint = json.dumps(payload, sort_keys=True)
        now = self._clock()
        fresh = self._last_emit is not None and now - self._last_emit < KEYFRAME_S
        if fingerprint == self._last and fresh:
            return 0
        self._last = fingerprint
        self._last_emit = now
        store.append(KIND, payload)
        return 1


def _snapshot_pane(agent: dict[str, Any]) -> dict[str, Any]:
    pane = {
        "pane_id": str(agent.get("pane_id") or ""),
        "cwd": str(agent.get("cwd") or ""),
        "agent": str(agent.get("agent") or ""),
        "status": str(agent.get("agent_status") or ""),
        "focused": bool(agent.get("focused")),
        "session_id": mux.herdr_session_id(agent),
        "title": sanitize_display(
            normalize_title(str(agent.get("terminal_title_stripped") or "")),
            max_len=_TITLE_MAX,
        ),
    }
    return {k: pane[k] for k in _PANE_KEYS}
