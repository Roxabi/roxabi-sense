"""Herdr live agent snapshot (structural fields only — no titles)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from roxabi_sense.store import Store
from roxabi_sense.util import mux

KIND = "herdr_snapshot"
_PANE_KEYS = ("pane_id", "cwd", "agent", "status", "focused", "session_id")


class HerdrSessionsCollector:
    name = "herdr"

    def __init__(
        self,
        *,
        list_agents: Callable[[], list[dict[str, Any]]] | None = None,
    ) -> None:
        self._list_agents = list_agents
        self._last: str | None = None

    def tick(self, store: Store) -> int:
        if self._list_agents is None and not mux.herdr_bin():
            return 0
        agents = self._list_agents() if self._list_agents is not None else mux.list_herdr_agents()
        panes = [_snapshot_pane(a) for a in agents]
        fingerprint = json.dumps(panes, sort_keys=True)
        if fingerprint == self._last:
            return 0
        self._last = fingerprint
        store.append(KIND, {"count": len(panes), "panes": panes})
        return 1


def _snapshot_pane(agent: dict[str, Any]) -> dict[str, Any]:
    pane = {
        "pane_id": str(agent.get("pane_id") or ""),
        "cwd": str(agent.get("cwd") or ""),
        "agent": str(agent.get("agent") or ""),
        "status": str(agent.get("agent_status") or ""),
        "focused": bool(agent.get("focused")),
        "session_id": mux.herdr_session_id(agent),
    }
    return {k: pane[k] for k in _PANE_KEYS}
