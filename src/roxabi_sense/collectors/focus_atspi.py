"""Focus / desktop facts from AT-SPI window lists (enrich + store).

Probing lives in `roxabi_sense.atspi` (long-lived agent). This collector only
dedups, resolves Unnamed→comm, attaches Grok/Claude sessions, and writes store rows.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from roxabi_sense.atspi import probe_once
from roxabi_sense.store import Store
from roxabi_sense.util.agent_link import find_agent_link, list_tmux_agent_panes
from roxabi_sense.util.proc import children_map, resolve_app_name
from roxabi_sense.util.session_registry import load_all_sessions
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


def raw_dicts_to_windows(rows: list[dict[str, Any]]) -> list[WindowInfo]:
    out: list[WindowInfo] = []
    for item in rows:
        pid_raw = item.get("pid")
        if isinstance(pid_raw, int):
            pid: int | None = pid_raw
        elif isinstance(pid_raw, str) and pid_raw.isdigit():
            pid = int(pid_raw)
        else:
            pid = None
        out.append(
            WindowInfo(
                app=str(item.get("app") or "unknown"),
                title=str(item.get("title") or ""),
                active=bool(item.get("active")),
                role=str(item.get("role") or ""),
                pid=pid,
            )
        )
    return out


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
        self._sessions_loader = sessions_loader or load_all_sessions
        self._last_desktop_fp: str | None = None
        self._last_focus_key: tuple[Any, ...] | None = None
        self.probe_count = 0
        self.last_probe_ms = 0

    def tick(self, store: Store) -> int:
        """Boot / poll / once: full inventory via one-shot probe."""
        return self._tick_from_probe(store, mode="full")

    def tick_focus(self, store: Store) -> int:
        return self._tick_from_probe(store, mode="focus")

    def tick_desktop(self, store: Store) -> int:
        return self._tick_from_probe(store, mode="desktop")

    def apply(
        self,
        store: Store,
        windows: list[WindowInfo] | list[dict[str, Any]],
        *,
        mode: TickMode,
        probe_ms: int | None = None,
    ) -> int:
        """Apply a window list from the long-lived agent (no subprocess)."""
        t0 = time.monotonic()
        raw: list[WindowInfo]
        if windows and isinstance(windows[0], dict):
            raw = raw_dicts_to_windows(
                [w for w in windows if isinstance(w, dict)]  # type: ignore[list-item]
            )
        else:
            raw = [w for w in windows if isinstance(w, WindowInfo)]
        return self._finish(store, raw, mode=mode, probe_ms=probe_ms, t0=t0)

    def _tick_from_probe(self, store: Store, *, mode: TickMode) -> int:
        t0 = time.monotonic()
        raw = self._probe_focus() if mode == "focus" else self._probe()
        return self._finish(store, raw, mode=mode, probe_ms=None, t0=t0)

    def _finish(
        self,
        store: Store,
        raw: list[WindowInfo],
        *,
        mode: TickMode,
        probe_ms: int | None,
        t0: float,
    ) -> int:
        windows = self._enrich(raw, focus_only=(mode == "focus"))
        self.last_probe_ms = (
            probe_ms if probe_ms is not None else int((time.monotonic() - t0) * 1000)
        )
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
        body: dict[str, Any] = {
            "app": active.app,
            "title": active.title,
            "source": "atspi",
        }
        if active.pid is not None:
            body["pid"] = active.pid
        if active.title_raw and active.title_raw != active.title:
            body["title_raw"] = active.title_raw
        if active.agent:
            body["agent"] = active.agent
        store.append(KIND, body)
        return 1

    def _enrich(self, windows: list[WindowInfo], *, focus_only: bool) -> list[WindowInfo]:
        sessions = self._sessions_loader()
        tree = children_map()
        # One tmux list-panes per enrich (not per window).
        panes = list_tmux_agent_panes()
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
                panes=panes,
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


def _default_probe_desktop() -> list[WindowInfo]:
    return raw_dicts_to_windows(probe_once("desktop"))


def _default_probe_focus() -> list[WindowInfo]:
    return raw_dicts_to_windows(probe_once("focus"))
