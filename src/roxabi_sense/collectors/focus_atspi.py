"""Focused / visible windows via AT-SPI (Cosmic / Ghostty-friendly).

- Resolve AT-SPI 'Unnamed' → /proc comm (ghostty)
- Dedup focus on (app, normalized_title, agent_key)
- Dual path: tick_focus (event, active-only) vs tick_desktop (rare full snapshot)
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from roxabi_sense.collectors.focus_atspi_probe import RawWindow, run_probe
from roxabi_sense.store import Store
from roxabi_sense.util.agent_link import find_agent_link, load_grok_sessions
from roxabi_sense.util.proc import children_map, resolve_app_name
from roxabi_sense.util.titles import normalize_title, sanitize_display

KIND = "focus"
SNAPSHOT = "desktop_snapshot"
TickMode = Literal["full", "focus", "desktop"]


@dataclass
class WindowInfo:
    app: str
    title: str
    active: bool
    role: str
    pid: int | None = None
    title_raw: str | None = None
    agent: dict[str, Any] | None = None


def _agent_key(agent: dict[str, Any] | None) -> str:
    if not agent:
        return ""
    return str(agent.get("session_id") or agent.get("pid") or agent.get("match") or "")


class FocusAtspiCollector:
    name = "focus_atspi"

    def __init__(
        self,
        probe: Callable[[], list[WindowInfo]] | None = None,
        *,
        probe_focus: Callable[[], list[WindowInfo]] | None = None,
        sessions_loader: Callable[[], list[dict[str, Any]]] | None = None,
    ) -> None:
        self._probe = probe or _default_probe_desktop
        self._probe_focus = probe_focus or probe or _default_probe_focus
        self._sessions_loader = sessions_loader or load_grok_sessions
        self._last_desktop_fp: str | None = None
        self._last_focus_key: tuple[Any, ...] | None = None
        self.probe_count = 0
        self.last_probe_ms = 0

    def tick(self, store: Store) -> int:
        """Boot / poll fallback: focus + desktop from full probe."""
        return self._tick(store, mode="full")

    def tick_focus(self, store: Store) -> int:
        """Event path: active window only → focus fact (no desktop_snapshot)."""
        return self._tick(store, mode="focus")

    def tick_desktop(self, store: Store) -> int:
        """Backup path: full inventory → desktop_snapshot (+ focus if changed)."""
        return self._tick(store, mode="desktop")

    def _tick(self, store: Store, *, mode: TickMode) -> int:
        t0 = time.monotonic()
        if mode == "focus":
            raw = self._probe_focus()
        else:
            raw = self._probe()
        windows = self._enrich(raw, focus_only=(mode == "focus"))
        self.last_probe_ms = int((time.monotonic() - t0) * 1000)
        self.probe_count += 1
        store.set_meta("focus_probe_count", str(self.probe_count))
        store.set_meta("focus_probe_last_ms", str(self.last_probe_ms))
        store.set_meta("focus_probe_last_mode", mode)

        wrote = 0
        if mode in {"full", "desktop"}:
            desktop_fp = _desktop_fingerprint(windows)
            if desktop_fp != self._last_desktop_fp:
                self._last_desktop_fp = desktop_fp
                store.append(SNAPSHOT, _desktop_payload(windows))
                wrote += 1

        if mode == "desktop":
            # Desktop backup also refreshes focus if the active window changed.
            wrote += self._maybe_write_focus(store, windows)
            return wrote

        if mode in {"full", "focus"}:
            wrote += self._maybe_write_focus(store, windows)
        return wrote

    def _maybe_write_focus(self, store: Store, windows: list[WindowInfo]) -> int:
        active = next((w for w in windows if w.active), None)
        if active is None:
            return 0
        focus_key = (active.app, active.title, active.pid, _agent_key(active.agent))
        if focus_key == self._last_focus_key:
            return 0
        self._last_focus_key = focus_key
        focus_body: dict[str, Any] = {
            "app": active.app,
            "title": active.title,
            "source": "atspi",
        }
        if active.pid is not None:
            focus_body["pid"] = active.pid
        if active.title_raw and active.title_raw != active.title:
            focus_body["title_raw"] = active.title_raw
        if active.agent:
            focus_body["agent"] = active.agent
        store.append(KIND, focus_body)
        return 1

    def _enrich(
        self, windows: list[WindowInfo], *, focus_only: bool
    ) -> list[WindowInfo]:
        sessions = self._sessions_loader()
        tree = children_map()
        out: list[WindowInfo] = []
        for w in windows:
            if focus_only and not w.active:
                continue
            app = resolve_app_name(w.app, w.pid)
            raw = sanitize_display(w.title)
            title = normalize_title(raw)
            agent = find_agent_link(
                w.pid,
                app=app,
                title=title,
                sessions=sessions,
                tree=tree,
            )
            out.append(
                WindowInfo(
                    app=app,
                    title=title,
                    active=w.active,
                    role=w.role,
                    pid=w.pid,
                    title_raw=raw if raw != title else None,
                    agent=agent,
                )
            )
        return out


def _desktop_fingerprint(windows: list[WindowInfo]) -> str:
    rows = sorted((w.app, w.title, w.active, w.pid, _agent_key(w.agent)) for w in windows)
    return json.dumps(rows, separators=(",", ":"))


def _desktop_payload(windows: list[WindowInfo]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "windows": [
            {
                "app": w.app,
                "title": w.title,
                "active": w.active,
                "role": w.role,
                **({"pid": w.pid} if w.pid is not None else {}),
                **({"title_raw": w.title_raw} if w.title_raw else {}),
                **({"agent": w.agent} if w.agent else {}),
            }
            for w in windows
        ],
        "source": "atspi",
    }
    active = next((w for w in windows if w.active), None)
    if active is not None:
        focus: dict[str, Any] = {"app": active.app, "title": active.title}
        if active.pid is not None:
            focus["pid"] = active.pid
        if active.agent:
            focus["agent"] = active.agent
        payload["focus"] = focus
    return payload


def _raw_to_window_info(rows: list[RawWindow]) -> list[WindowInfo]:
    return [
        WindowInfo(
            app=r.app, title=r.title, active=r.active, role=r.role, pid=r.pid
        )
        for r in rows
    ]


def _default_probe_desktop() -> list[WindowInfo]:
    return _raw_to_window_info(run_probe(focus_only=False))


def _default_probe_focus() -> list[WindowInfo]:
    return _raw_to_window_info(run_probe(focus_only=True))
