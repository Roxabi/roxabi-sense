"""X11 FocusProbe via xprop / xwininfo (classic active-window path)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping
from typing import Any

from roxabi_sense.collectors.focus.protocol import FocusWindow

RunFn = Callable[..., subprocess.CompletedProcess[str]]

_XPROP_CANDIDATES = ("/usr/bin/xprop", "/bin/xprop")
_WID_RE = re.compile(r"^0x[0-9a-fA-F]+$")


def _default_run(
    args: list[str], *, timeout: float = 2.0
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


class X11FocusProbe:
    source: str = "x11"

    def __init__(
        self,
        *,
        env: Mapping[str, str] | None = None,
        run: RunFn | None = None,
        which: Callable[[str], str | None] | None = None,
    ) -> None:
        self._env: Mapping[str, str] | None = env
        self._run = run or _default_run
        self._which = which or shutil.which

    def _environ(self) -> Mapping[str, str]:
        return self._env if self._env is not None else os.environ

    def _xprop_bin(self) -> str | None:
        """Absolute path to xprop (prefer known paths, then which)."""
        for cand in _XPROP_CANDIDATES:
            if os.path.isfile(cand) and os.access(cand, os.X_OK):
                return cand
        found = self._which("xprop")
        if found and os.path.isabs(found):
            return found
        if found:
            # which may return a relative path — resolve via PATH once
            return found if os.path.isfile(found) else None
        return None

    def probe(self) -> bool:
        display = self._environ().get("DISPLAY")
        if not display:
            return False
        xprop = self._xprop_bin()
        if not xprop:
            return False
        try:
            proc = self._run([xprop, "-root", "_NET_ACTIVE_WINDOW"], timeout=2.0)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return proc.returncode == 0 and bool(proc.stdout.strip())

    def get_active(self) -> list[FocusWindow]:
        win = self._active_window()
        if win is None:
            return []
        return [win]

    def get_desktop(self) -> list[FocusWindow]:
        return self.get_active()

    def _active_window(self) -> FocusWindow | None:
        xprop = self._xprop_bin()
        if not xprop or not self._environ().get("DISPLAY"):
            return None
        try:
            root = self._run([xprop, "-root", "_NET_ACTIVE_WINDOW"], timeout=2.0)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if root.returncode != 0:
            return None
        wid = _parse_window_id(root.stdout)
        if wid is None or not _WID_RE.fullmatch(wid):
            return None
        try:
            props = self._run(
                [
                    xprop,
                    "-id",
                    wid,
                    "WM_CLASS",
                    "_NET_WM_NAME",
                    "WM_NAME",
                    "_NET_WM_PID",
                ],
                timeout=2.0,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        parsed = _parse_xprop(props.stdout)
        app = parsed.get("app") or "unknown"
        title = parsed.get("title") or ""
        pid = parsed.get("pid")
        return FocusWindow(app=app, title=title, active=True, role="frame", pid=pid)


def _parse_window_id(text: str) -> str | None:
    # _NET_ACTIVE_WINDOW(WINDOW): window id # 0x3c00007
    m = re.search(r"window id # (0x[0-9a-fA-F]+)", text)
    if m:
        wid = m.group(1)
        if wid.lower() in {"0x0", "0x00"}:
            return None
        return wid
    return None


def _parse_xprop(text: str) -> dict[str, Any]:
    app: str | None = None
    title: str | None = None
    pid: int | None = None
    for line in text.splitlines():
        if "WM_CLASS" in line and "=" in line:
            # WM_CLASS(STRING) = "code", "Code"
            parts = re.findall(r'"([^"]*)"', line)
            if parts:
                app = parts[-1] or parts[0]
        elif "_NET_WM_NAME" in line and "=" in line:
            m = re.search(r'=\s*"(.*)"\s*$', line)
            if m:
                title = m.group(1)
            else:
                # UTF8_STRING without quotes sometimes
                m2 = re.search(r"=\s*(.+)$", line)
                if m2:
                    title = m2.group(1).strip().strip('"')
        elif "WM_NAME" in line and title is None and "=" in line:
            m = re.search(r'=\s*"(.*)"\s*$', line)
            if m:
                title = m.group(1)
        elif "_NET_WM_PID" in line and "=" in line:
            m = re.search(r"=\s*(\d+)", line)
            if m:
                pid = int(m.group(1))
    return {"app": app, "title": title, "pid": pid}
