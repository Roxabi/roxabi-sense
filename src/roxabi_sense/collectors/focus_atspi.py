"""Focused / visible windows via AT-SPI (Cosmic / Ghostty-friendly).

- Resolve AT-SPI 'Unnamed' → /proc comm (ghostty)
- Dedup focus on (app, normalized_title, agent_key)
- Desktop snapshot fingerprint includes agent identity
- Attach agent link (grok session) via process tree / tmux+title
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from roxabi_sense.store import Store
from roxabi_sense.util.agent_link import find_agent_link, load_grok_sessions
from roxabi_sense.util.proc import children_map, resolve_app_name
from roxabi_sense.util.titles import normalize_title, sanitize_display

KIND = "focus"
SNAPSHOT = "desktop_snapshot"

_PROBE_SCRIPT = r"""
import json
import sys
try:
    import gi
    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi
except Exception as e:
    print(json.dumps({"error": f"gi:{e}", "windows": []}))
    sys.exit(0)

try:
    Atspi.init()
    desktop = Atspi.get_desktop(0)
except Exception as e:
    print(json.dumps({"error": f"init:{e}", "windows": []}))
    sys.exit(0)

windows = []
try:
    n_apps = desktop.get_child_count()
except Exception as e:
    print(json.dumps({"error": f"desktop:{e}", "windows": []}))
    sys.exit(0)

for i in range(n_apps):
    try:
        app = desktop.get_child_at_index(i)
        if app is None:
            continue
        app_name = app.get_name() or "unknown"
        pid = None
        try:
            if hasattr(app, "get_process_id"):
                pid = int(app.get_process_id())
        except Exception:
            pid = None
        n_win = app.get_child_count()
        for j in range(n_win):
            frame = app.get_child_at_index(j)
            if frame is None:
                continue
            role = frame.get_role_name() or ""
            if role not in {"frame", "window"}:
                continue
            title = frame.get_name() or ""
            states = {}
            try:
                st = frame.get_state_set()
                for name in ("ACTIVE", "FOCUSED", "SHOWING", "VISIBLE", "ICONIFIED"):
                    stype = getattr(Atspi.StateType, name, None)
                    if stype is not None:
                        states[name] = bool(st.contains(stype))
            except Exception:
                pass
            if not (states.get("SHOWING") or states.get("VISIBLE") or states.get("ACTIVE")):
                continue
            windows.append({
                "app": app_name,
                "title": title,
                "active": bool(states.get("ACTIVE")),
                "role": role,
                "pid": pid,
            })
    except Exception:
        continue

print(json.dumps({"windows": windows}))
"""


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
        sessions_loader: Callable[[], list[dict[str, Any]]] | None = None,
    ) -> None:
        self._probe = probe or _default_probe
        self._sessions_loader = sessions_loader or load_grok_sessions
        self._last_desktop_fp: str | None = None
        self._last_focus_key: tuple[Any, ...] | None = None

    def tick(self, store: Store) -> int:
        windows = self._enrich(self._probe())
        wrote = 0

        desktop_fp = _desktop_fingerprint(windows)
        if desktop_fp != self._last_desktop_fp:
            self._last_desktop_fp = desktop_fp
            store.append(SNAPSHOT, _desktop_payload(windows))
            wrote += 1

        active = next((w for w in windows if w.active), None)
        if active is None:
            return wrote

        focus_key = (active.app, active.title, active.pid, _agent_key(active.agent))
        if focus_key == self._last_focus_key:
            return wrote
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
        return wrote + 1

    def _enrich(self, windows: list[WindowInfo]) -> list[WindowInfo]:
        sessions = self._sessions_loader()
        tree = children_map()
        out: list[WindowInfo] = []
        for w in windows:
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


def _system_python() -> str:
    for candidate in ("/usr/bin/python3", shutil.which("python3") or ""):
        if candidate and candidate != sys.executable:
            return candidate
    return sys.executable


def _default_probe() -> list[WindowInfo]:
    py = _system_python()
    try:
        proc = subprocess.run(
            [py, "-c", _PROBE_SCRIPT],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    line = (proc.stdout or "").strip().splitlines()
    if not line:
        return []
    try:
        data = json.loads(line[-1])
    except json.JSONDecodeError:
        return []
    raw = data.get("windows") or []
    out: list[WindowInfo] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
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
