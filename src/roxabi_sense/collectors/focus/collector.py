"""Focus / desktop facts: enrich + store (backend via FocusProbe)."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any, Literal

from roxabi_sense.collectors.focus.probes.atspi import AtspiFocusProbe
from roxabi_sense.collectors.focus.protocol import FocusProbe, FocusWindow, raw_dicts_to_windows
from roxabi_sense.store import Store
from roxabi_sense.util.agent_link import find_agent_link, list_tmux_agent_panes
from roxabi_sense.util.proc import children_map, resolve_app_name
from roxabi_sense.util.session_registry import load_all_sessions
from roxabi_sense.util.titles import normalize_title, sanitize_display

KIND = "focus"
SNAPSHOT = "desktop_snapshot"
TickMode = Literal["full", "focus", "desktop"]

# Back-compat alias used by tests / older imports
WindowInfo = FocusWindow


def _agent_key(agent: dict[str, Any] | None) -> str:
    if not agent:
        return ""
    return str(agent.get("session_id") or agent.get("pid") or agent.get("match") or "")


class FocusCollector:
    """Dedup, resolve Unnamed→comm, attach agent sessions, write store rows."""

    name = "focus_atspi"  # stable collector id (poll lists / tests)

    def __init__(
        self,
        probe: Callable[[], list[FocusWindow]] | None = None,
        *,
        probe_focus: Callable[[], list[FocusWindow]] | None = None,
        sessions_loader: Callable[[], list[dict[str, Any]]] | None = None,
        focus_probe: FocusProbe | None = None,
        source: str | None = None,
    ) -> None:
        self._focus_probe = focus_probe
        self._source_override = source
        # Legacy callables (tests inject list[WindowInfo] factories)
        self._probe = probe
        self._probe_focus = probe_focus or probe
        self._sessions_loader = sessions_loader or load_all_sessions
        self._last_desktop_fp: str | None = None
        self._last_focus_key: tuple[Any, ...] | None = None
        self.probe_count = 0
        self.last_probe_ms = 0

    @property
    def backend_source(self) -> str:
        if self._source_override:
            return self._source_override
        if self._focus_probe is not None:
            return str(self._focus_probe.source)
        return "atspi"

    def set_focus_probe(self, probe: FocusProbe | None) -> None:
        self._focus_probe = probe
        self._source_override = None

    def set_source(self, source: str) -> None:
        self._source_override = source

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
        windows: list[FocusWindow] | list[dict[str, Any]],
        *,
        mode: TickMode,
        probe_ms: int | None = None,
        source: str | None = None,
    ) -> int:
        """Apply a window list from the long-lived agent (no subprocess).

        ``source`` applies only to this write — does not sticky-override later polls.
        """
        t0 = time.monotonic()
        raw: list[FocusWindow]
        if windows and isinstance(windows[0], dict):
            raw = raw_dicts_to_windows(
                [w for w in windows if isinstance(w, dict)]  # type: ignore[list-item]
            )
        else:
            raw = [w for w in windows if isinstance(w, FocusWindow)]
        return self._finish(
            store, raw, mode=mode, probe_ms=probe_ms, t0=t0, source=source
        )

    def _tick_from_probe(self, store: Store, *, mode: TickMode) -> int:
        t0 = time.monotonic()
        raw = self._read_windows(mode)
        return self._finish(store, raw, mode=mode, probe_ms=None, t0=t0, source=None)

    def _read_windows(self, mode: TickMode) -> list[FocusWindow]:
        if self._probe is not None or self._probe_focus is not None:
            if mode == "focus" and self._probe_focus is not None:
                return list(self._probe_focus())
            if self._probe is not None:
                return list(self._probe())
            if self._probe_focus is not None:
                return list(self._probe_focus())
        fp = self._focus_probe or AtspiFocusProbe()
        if mode == "focus":
            return fp.get_active()
        return fp.get_desktop()

    def _finish(
        self,
        store: Store,
        raw: list[FocusWindow],
        *,
        mode: TickMode,
        probe_ms: int | None,
        t0: float,
        source: str | None,
    ) -> int:
        windows = self._enrich(raw, focus_only=(mode == "focus"))
        self.last_probe_ms = (
            probe_ms if probe_ms is not None else int((time.monotonic() - t0) * 1000)
        )
        self.probe_count += 1
        store.set_meta("focus_probe_count", str(self.probe_count))
        store.set_meta("focus_probe_last_ms", str(self.last_probe_ms))
        store.set_meta("focus_probe_last_mode", mode)
        src = source if source is not None else self.backend_source

        wrote = 0
        if mode in {"full", "desktop"}:
            # ADR-004: probes soft-fail as [] — never emit empty inventory (false
            # meeting clear). Hangup still clears via non-empty desktop without Meet.
            if windows:
                desktop_fp = _desktop_fingerprint(windows)
                if desktop_fp != self._last_desktop_fp:
                    self._last_desktop_fp = desktop_fp
                    store.append(SNAPSHOT, _desktop_payload(windows, source=src))
                    wrote += 1
        if mode == "desktop":
            wrote += self._maybe_write_focus(store, windows, source=src)
            return wrote
        if mode in {"full", "focus"}:
            wrote += self._maybe_write_focus(store, windows, source=src)
        return wrote

    def _maybe_write_focus(
        self, store: Store, windows: list[FocusWindow], *, source: str
    ) -> int:
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
            "source": source,
        }
        if active.pid is not None:
            body["pid"] = active.pid
        if active.title_raw and active.title_raw != active.title:
            body["title_raw"] = active.title_raw
        if active.agent:
            body["agent"] = active.agent
        store.append(KIND, body)
        return 1

    def _enrich(self, windows: list[FocusWindow], *, focus_only: bool) -> list[FocusWindow]:
        sessions = self._sessions_loader()
        tree = children_map()
        panes = list_tmux_agent_panes()
        out: list[FocusWindow] = []
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
                FocusWindow(
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


def _desktop_fingerprint(windows: list[FocusWindow]) -> str:
    rows = sorted((w.app, w.title, w.active, w.pid, _agent_key(w.agent)) for w in windows)
    return json.dumps(rows, separators=(",", ":"))


def _desktop_payload(windows: list[FocusWindow], *, source: str) -> dict[str, Any]:
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
        "source": source,
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
