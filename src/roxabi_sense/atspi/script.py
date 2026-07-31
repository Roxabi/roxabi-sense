"""Build the long-lived AT-SPI agent subprocess source (testable)."""

from __future__ import annotations

from typing import Literal

NameEventsMode = Literal["off", "throttled", "on"]


def build_agent_script(
    *,
    name_events: NameEventsMode = "throttled",
    name_throttle_s: float = 10.0,
    probe_min_s: float = 0.5,
) -> str:
    name_mode = name_events if name_events in {"off", "throttled", "on"} else "throttled"
    name_ms = max(0, int(float(name_throttle_s) * 1000))
    probe_ms = max(50, int(float(probe_min_s) * 1000))
    register_name = name_mode != "off"
    return f'''
import json, os, sys, time, threading
try:
    import gi
    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi, GLib
except Exception as e:
    print(json.dumps({{"type": "error", "error": f"gi:{{e}}"}}), flush=True)
    sys.exit(1)

_NAME_MODE = {name_mode!r}
_NAME_THROTTLE_MS = {name_ms}
_PROBE_MIN_MS = {probe_ms}
_last_probe_ms = 0.0
_trail_at = None
_name_last = 0.0
_name_trail = False
_pending = False
_pending_reason = "activate"
_loop = None

def emit(obj):
    print(json.dumps(obj), flush=True)

def walk(focus_only):
    windows = []
    try:
        desktop = Atspi.get_desktop(0)
        n_apps = desktop.get_child_count()
    except Exception as e:
        emit({{"type": "error", "error": f"desktop:{{e}}"}})
        return windows
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
            for j in range(app.get_child_count()):
                frame = app.get_child_at_index(j)
                if frame is None:
                    continue
                role = frame.get_role_name() or ""
                if role not in {{"frame", "window"}}:
                    continue
                states = {{}}
                try:
                    st = frame.get_state_set()
                    for nm in ("ACTIVE", "SHOWING", "VISIBLE"):
                        stype = getattr(Atspi.StateType, nm, None)
                        if stype is not None:
                            states[nm] = bool(st.contains(stype))
                except Exception:
                    pass
                active = bool(states.get("ACTIVE"))
                if focus_only:
                    if not active:
                        continue
                    title = frame.get_name() or ""
                    windows.append({{
                        "app": app_name, "title": title, "active": True,
                        "role": role, "pid": pid,
                    }})
                    return windows
                if not (states.get("SHOWING") or states.get("VISIBLE") or active):
                    continue
                title = frame.get_name() or ""
                windows.append({{
                    "app": app_name, "title": title, "active": active,
                    "role": role, "pid": pid,
                }})
        except Exception:
            continue
    return windows

def do_probe(mode, reason):
    global _last_probe_ms
    t0 = time.monotonic()
    focus_only = mode == "focus"
    wins = walk(focus_only)
    ms = int((time.monotonic() - t0) * 1000)
    _last_probe_ms = time.monotonic() * 1000.0
    emit({{
        "type": "probe_result", "mode": mode, "reason": reason,
        "windows": wins, "ms": ms, "source": "atspi",
    }})

def schedule_focus(reason):
    global _pending, _pending_reason, _trail_at
    now = time.monotonic() * 1000.0
    if now - _last_probe_ms >= _PROBE_MIN_MS:
        _trail_at = None
        do_probe("focus", reason)
        return
    _trail_at = _last_probe_ms + _PROBE_MIN_MS
    _pending_reason = reason if reason == "activate" else (_pending_reason or reason)

def trail_tick():
    global _trail_at
    if _trail_at is None:
        return True
    now = time.monotonic() * 1000.0
    if now >= _trail_at:
        reason = _pending_reason or "activate"
        _trail_at = None
        do_probe("focus", reason)
    return True

def flush_coalesce():
    global _pending, _pending_reason
    reason = _pending_reason
    _pending = False
    _pending_reason = "activate"
    schedule_focus(reason)
    return False

def arm(reason):
    global _pending, _pending_reason
    if _pending:
        if reason == "activate":
            _pending_reason = "activate"
        return
    _pending = True
    _pending_reason = reason
    GLib.timeout_add(80, flush_coalesce)

def name_trail_flush():
    global _name_trail
    _name_trail = False
    arm("name")
    return False

def event_reason(event):
    try:
        et = str(getattr(event, "type", "") or "")
    except Exception:
        et = ""
    if "accessible-name" in et or "property-change" in et:
        return "name"
    return "activate"

def on_event(event):
    global _name_last, _name_trail
    reason = event_reason(event)
    if reason == "name":
        if _NAME_MODE == "off":
            return
        if _NAME_MODE == "throttled":
            now = time.monotonic() * 1000.0
            if now - _name_last >= _NAME_THROTTLE_MS:
                _name_last = now
                arm("name")
            elif not _name_trail:
                _name_trail = True
                rem = max(1, int(_NAME_THROTTLE_MS - (now - _name_last)))
                GLib.timeout_add(rem, name_trail_flush)
            return
    arm(reason)

def handle_cmd(msg):
    if not isinstance(msg, dict):
        return False
    cmd = msg.get("cmd")
    if cmd == "quit":
        if _loop is not None:
            _loop.quit()
        return False
    if cmd == "probe":
        mode = msg.get("mode") or "desktop"
        if mode not in ("focus", "desktop"):
            mode = "desktop"
        do_probe(mode, "cmd")
    return False

def stdin_loop():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        GLib.idle_add(handle_cmd, msg)

once = os.environ.get("SENSE_ATSPI_ONCE", "").strip()
try:
    Atspi.init()
except Exception as e:
    emit({{"type": "error", "error": f"init:{{e}}"}})
    sys.exit(1)

if once in ("focus", "desktop"):
    do_probe(once, "once")
    sys.exit(0)

listener = Atspi.EventListener.new(on_event)
_types = [
    "window:activate", "window:create", "window:destroy",
    "object:state-changed:active",
]
if {register_name!r}:
    _types.append("object:property-change:accessible-name")
for t in _types:
    try:
        listener.register(t)
    except Exception as e:
        emit({{"type": "warn", "warn": f"register {{t}}: {{e}}"}})

emit({{"type": "ready", "source": "atspi", "name_events": _NAME_MODE}})
GLib.timeout_add(50, trail_tick)
threading.Thread(target=stdin_loop, name="atspi-stdin", daemon=True).start()
_loop = GLib.MainLoop()
try:
    _loop.run()
except KeyboardInterrupt:
    pass
'''
