"""AT-SPI desktop / focus-only probes (system python + gi)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass


@dataclass
class RawWindow:
    app: str
    title: str
    active: bool
    role: str
    pid: int | None = None


# Full desktop inventory (backup / boot / poll).
_PROBE_DESKTOP = r"""
import json, sys
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
                "app": app_name, "title": title,
                "active": bool(states.get("ACTIVE")), "role": role, "pid": pid,
            })
    except Exception:
        continue
print(json.dumps({"windows": windows}))
"""

# Event path: stop once ACTIVE frame found (skip rest of desktop).
_PROBE_FOCUS = r"""
import json, sys
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
            states = {}
            try:
                st = frame.get_state_set()
                for name in ("ACTIVE", "SHOWING", "VISIBLE"):
                    stype = getattr(Atspi.StateType, name, None)
                    if stype is not None:
                        states[name] = bool(st.contains(stype))
            except Exception:
                pass
            if not states.get("ACTIVE"):
                continue
            title = frame.get_name() or ""
            windows.append({
                "app": app_name, "title": title,
                "active": True, "role": role, "pid": pid,
            })
            print(json.dumps({"windows": windows}))
            sys.exit(0)
    except Exception:
        continue
print(json.dumps({"windows": windows}))
"""


def _system_python() -> str:
    for candidate in ("/usr/bin/python3", shutil.which("python3") or ""):
        if candidate and candidate != sys.executable:
            return candidate
    return sys.executable


def _parse_windows(stdout: str) -> list[RawWindow]:
    line = (stdout or "").strip().splitlines()
    if not line:
        return []
    try:
        data = json.loads(line[-1])
    except json.JSONDecodeError:
        return []
    raw = data.get("windows") or []
    out: list[RawWindow] = []
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
            RawWindow(
                app=str(item.get("app") or "unknown"),
                title=str(item.get("title") or ""),
                active=bool(item.get("active")),
                role=str(item.get("role") or ""),
                pid=pid,
            )
        )
    return out


def run_probe(*, focus_only: bool = False, timeout: float = 5.0) -> list[RawWindow]:
    script = _PROBE_FOCUS if focus_only else _PROBE_DESKTOP
    try:
        proc = subprocess.run(
            [_system_python(), "-c", script],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return _parse_windows(proc.stdout or "")
