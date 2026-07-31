#!/usr/bin/env python3
"""Standalone AT-SPI agent for system python (gi). Config via env SENSE_ATSPI_*."""

from __future__ import annotations

import json
import os
import sys
import threading
import time

try:
    import gi

    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi, GLib
except Exception as e:  # noqa: BLE001
    print(json.dumps({"type": "error", "error": f"gi:{e}"}), flush=True)
    sys.exit(1)

_NAME_MODE = os.environ.get("SENSE_ATSPI_NAME_MODE", "throttled")
_NAME_THROTTLE_MS = int(os.environ.get("SENSE_ATSPI_NAME_MS", "10000"))
_PROBE_MIN_MS = int(os.environ.get("SENSE_ATSPI_PROBE_MS", "500"))
_TRACE = os.environ.get("SENSE_ATSPI_TRACE", "0") in {"1", "true", "yes"}
_REGISTER_NAME = _TRACE or _NAME_MODE != "off"

_last_probe_ms = 0.0
_trail_at = None
_name_last = 0.0
_name_trail = False
_pending = False
_pending_reason = "activate"
_pending_win: dict | None = None  # focus from event.source (not desktop walk)
_loop = None
_trace_name_last = 0.0


def emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def states_of(obj) -> dict:
    out: dict = {}
    try:
        st = obj.get_state_set()
        for nm in ("ACTIVE", "FOCUSED", "SHOWING", "VISIBLE", "ICONIFIED"):
            stype = getattr(Atspi.StateType, nm, None)
            if stype is not None:
                out[nm.lower()] = bool(st.contains(stype))
    except Exception:  # noqa: BLE001
        pass
    return out


def describe_src(event) -> dict:
    src: dict = {"event": str(getattr(event, "type", "") or "")}
    try:
        obj = getattr(event, "source", None)
    except Exception:  # noqa: BLE001
        obj = None
    if obj is None:
        return src
    try:
        src["role"] = obj.get_role_name() or ""
    except Exception:  # noqa: BLE001
        src["role"] = ""
    try:
        src["name"] = obj.get_name() or ""
    except Exception:  # noqa: BLE001
        src["name"] = ""
    app_name, pid = "unknown", None
    try:
        cur = obj
        for _ in range(12):
            if cur is None:
                break
            try:
                role = cur.get_role_name() or ""
            except Exception:  # noqa: BLE001
                role = ""
            if role in ("application", "desktop frame"):
                try:
                    app_name = cur.get_name() or app_name
                except Exception:  # noqa: BLE001
                    pass
                try:
                    if hasattr(cur, "get_process_id"):
                        pid = int(cur.get_process_id())
                except Exception:  # noqa: BLE001
                    pass
                break
            try:
                cur = cur.get_parent()
            except Exception:  # noqa: BLE001
                break
    except Exception:  # noqa: BLE001
        pass
    src["app"] = app_name
    if pid is not None:
        src["pid"] = pid
    src["states"] = states_of(obj)
    try:
        cur = obj
        for _ in range(8):
            if cur is None:
                break
            role = cur.get_role_name() or ""
            if role in ("frame", "window"):
                src["frame_name"] = cur.get_name() or ""
                src["frame_states"] = states_of(cur)
                break
            cur = cur.get_parent()
    except Exception:  # noqa: BLE001
        pass
    return src


def walk(focus_only: bool, with_focused: bool = False) -> list:
    windows: list = []
    try:
        desktop = Atspi.get_desktop(0)
        n_apps = desktop.get_child_count()
    except Exception as e:  # noqa: BLE001
        emit({"type": "error", "error": f"desktop:{e}"})
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
            except Exception:  # noqa: BLE001
                pid = None
            for j in range(app.get_child_count()):
                frame = app.get_child_at_index(j)
                if frame is None:
                    continue
                role = frame.get_role_name() or ""
                if role not in {"frame", "window"}:
                    continue
                st = states_of(frame)
                active = bool(st.get("active"))
                if focus_only and not with_focused:
                    if not active:
                        continue
                    windows.append(
                        {
                            "app": app_name,
                            "title": frame.get_name() or "",
                            "active": True,
                            "role": role,
                            "pid": pid,
                        }
                    )
                    return windows
                if focus_only and with_focused:
                    if not active:
                        continue
                    windows.append(
                        {
                            "app": app_name,
                            "title": frame.get_name() or "",
                            "active": True,
                            "focused": bool(st.get("focused")),
                            "states": st,
                            "role": role,
                            "pid": pid,
                        }
                    )
                    continue
                if not (st.get("showing") or st.get("visible") or active):
                    continue
                row = {
                    "app": app_name,
                    "title": frame.get_name() or "",
                    "active": active,
                    "role": role,
                    "pid": pid,
                }
                if with_focused:
                    row["focused"] = bool(st.get("focused"))
                    row["states"] = st
                windows.append(row)
        except Exception:  # noqa: BLE001
            continue
    return windows


def window_from_src(src: dict) -> dict | None:
    """Build a single focus window from event.source — desktop walk not used."""
    app = str(src.get("app") or "unknown")
    title = str(src.get("frame_name") or src.get("name") or "")
    pid = src.get("pid")
    if app in {"unknown", ""} and not title and pid is None:
        return None
    if isinstance(pid, str) and pid.isdigit():
        pid = int(pid)
    elif not isinstance(pid, int):
        pid = None
    return {
        "app": app or "unknown",
        "title": title,
        "active": True,
        "role": "frame",
        "pid": pid,
        "focus_via": "event_source",
    }


def emit_focus_win(win: dict, reason: str) -> None:
    """Emit focus fact from event source (no ACTIVE walk / first-wins)."""
    global _last_probe_ms
    _last_probe_ms = time.monotonic() * 1000.0
    emit(
        {
            "type": "probe_result",
            "mode": "focus",
            "reason": reason,
            "windows": [win],
            "ms": 0,
            "source": "atspi",
            "focus_via": "event_source",
        }
    )


def do_probe(mode: str, reason: str) -> None:
    """Desktop/full inventory only (backup, once). Focus path uses emit_focus_win."""
    global _last_probe_ms
    t0 = time.monotonic()
    # Never use focus_only walk for product truth — multi-ACTIVE lies.
    wins = walk(False) if mode in {"desktop", "full", "focus"} else walk(False)
    if mode == "focus":
        # Fallback only for once/cmd: prefer single ACTIVE if exactly one, else all active
        act = [w for w in wins if w.get("active")]
        wins = act[:1] if len(act) == 1 else (act[:1] if act else wins[:1])
    ms = int((time.monotonic() - t0) * 1000)
    _last_probe_ms = time.monotonic() * 1000.0
    emit(
        {
            "type": "probe_result",
            "mode": mode if mode != "focus" else "focus",
            "reason": reason,
            "windows": wins,
            "ms": ms,
            "source": "atspi",
            "focus_via": "walk_fallback",
        }
    )


def schedule_focus_win(win: dict, reason: str) -> None:
    global _trail_at, _pending_reason, _pending_win
    now = time.monotonic() * 1000.0
    if now - _last_probe_ms >= _PROBE_MIN_MS:
        _trail_at = None
        emit_focus_win(win, reason)
        return
    _trail_at = _last_probe_ms + _PROBE_MIN_MS
    _pending_reason = reason if reason == "activate" else (_pending_reason or reason)
    _pending_win = win


def trail_tick() -> bool:
    global _trail_at, _pending_win
    if _trail_at is None:
        return True
    now = time.monotonic() * 1000.0
    if now >= _trail_at:
        reason = _pending_reason or "activate"
        win = _pending_win
        _trail_at = None
        _pending_win = None
        if win:
            emit_focus_win(win, reason)
    return True


def flush_coalesce() -> bool:
    global _pending, _pending_reason, _pending_win
    reason = _pending_reason
    win = _pending_win
    _pending = False
    _pending_reason = "activate"
    _pending_win = None
    if win:
        schedule_focus_win(win, reason)
    return False


def arm_win(win: dict, reason: str) -> None:
    global _pending, _pending_reason, _pending_win
    if _pending:
        # Prefer activate over name; always keep latest source window.
        if reason == "activate" or _pending_reason != "activate":
            _pending_reason = reason if reason == "activate" else _pending_reason
            _pending_win = win
        return
    _pending = True
    _pending_reason = reason
    _pending_win = win
    GLib.timeout_add(80, flush_coalesce)


def name_trail_flush() -> bool:
    global _name_trail, _pending_win
    _name_trail = False
    if _pending_win:
        arm_win(_pending_win, "name")
    return False


def event_reason(event) -> str:
    try:
        et = str(getattr(event, "type", "") or "")
    except Exception:  # noqa: BLE001
        et = ""
    if "accessible-name" in et or "property-change" in et:
        return "name"
    return "activate"


def trace_event(event) -> None:
    global _trace_name_last
    et = str(getattr(event, "type", "") or "")
    if "accessible-name" in et or "property-change" in et:
        now = time.monotonic()
        if now - _trace_name_last < 0.2:
            return
        _trace_name_last = now
    src = describe_src(event)
    actives = []
    try:
        for w in walk(False, with_focused=True):
            if w.get("active") or w.get("focused"):
                actives.append(w)
    except Exception:  # noqa: BLE001
        pass
    emit(
        {
            "type": "atspi_raw",
            "event": et,
            "source": src,
            "actives": actives,
            "n_actives": len(actives),
            "wall": time.time(),
        }
    )


def on_event(event) -> None:
    global _name_last, _name_trail, _pending_win
    if _TRACE:
        try:
            trace_event(event)
        except Exception as e:  # noqa: BLE001
            emit({"type": "warn", "warn": f"trace:{e}"})
    reason = event_reason(event)
    src = describe_src(event)
    win = window_from_src(src)
    if win is None:
        return
    if reason == "name":
        if _NAME_MODE == "off":
            return
        if _NAME_MODE == "throttled":
            now = time.monotonic() * 1000.0
            if now - _name_last >= _NAME_THROTTLE_MS:
                _name_last = now
                arm_win(win, "name")
            elif not _name_trail:
                _name_trail = True
                _pending_win = win
                rem = max(1, int(_NAME_THROTTLE_MS - (now - _name_last)))
                GLib.timeout_add(rem, name_trail_flush)
            return
        arm_win(win, "name")
        return
    # activate / state-changed:active / create — truth = event source
    arm_win(win, "activate")


def handle_cmd(msg) -> bool:
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


def stdin_loop() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        GLib.idle_add(handle_cmd, msg)


def main() -> None:
    global _loop
    once = os.environ.get("SENSE_ATSPI_ONCE", "").strip()
    try:
        Atspi.init()
    except Exception as e:  # noqa: BLE001
        emit({"type": "error", "error": f"init:{e}"})
        sys.exit(1)
    if once in ("focus", "desktop"):
        do_probe(once, "once")
        sys.exit(0)
    listener = Atspi.EventListener.new(on_event)
    types = [
        "window:activate",
        "window:create",
        "window:destroy",
        "object:state-changed:active",
    ]
    if _REGISTER_NAME:
        types.append("object:property-change:accessible-name")
    for t in types:
        try:
            listener.register(t)
        except Exception as e:  # noqa: BLE001
            emit({"type": "warn", "warn": f"register {t}: {e}"})
    emit(
        {
            "type": "ready",
            "source": "atspi",
            "name_events": _NAME_MODE,
            "trace": bool(_TRACE),
        }
    )
    GLib.timeout_add(50, trail_tick)
    threading.Thread(target=stdin_loop, name="atspi-stdin", daemon=True).start()
    _loop = GLib.MainLoop()
    try:
        _loop.run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
