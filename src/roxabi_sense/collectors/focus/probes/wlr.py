"""wlroots FocusProbe — Hyprland (hyprctl) / Sway (swaymsg)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable, Mapping
from typing import Any

from roxabi_sense.collectors.focus.protocol import FocusWindow

RunFn = Callable[..., subprocess.CompletedProcess[str]]

_HYPR_CANDIDATES = ("/usr/bin/hyprctl", "/bin/hyprctl")
_SWAY_CANDIDATES = ("/usr/bin/swaymsg", "/bin/swaymsg")


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


class WlrFocusProbe:
    """Active window via compositor CLI (source=wlr)."""

    source: str = "wlr"

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

    def probe(self) -> bool:
        return self._backend() is not None

    def get_active(self) -> list[FocusWindow]:
        win = self._active_window()
        return [win] if win is not None else []

    def get_desktop(self) -> list[FocusWindow]:
        return self.get_active()

    def _backend(self) -> str | None:
        e = self._environ()
        if e.get("HYPRLAND_INSTANCE_SIGNATURE") and self._bin(
            "hyprctl", _HYPR_CANDIDATES
        ):
            return "hypr"
        if e.get("SWAYSOCK") and self._bin("swaymsg", _SWAY_CANDIDATES):
            return "sway"
        # Desktop family hint without socket vars: still try if bins exist
        if self._bin("hyprctl", _HYPR_CANDIDATES) and e.get("XDG_CURRENT_DESKTOP", "").lower().find(
            "hypr"
        ) >= 0:
            return "hypr"
        if self._bin("swaymsg", _SWAY_CANDIDATES) and "sway" in e.get(
            "XDG_CURRENT_DESKTOP", ""
        ).lower():
            return "sway"
        return None

    def _bin(self, name: str, candidates: tuple[str, ...]) -> str | None:
        for cand in candidates:
            if os.path.isfile(cand) and os.access(cand, os.X_OK):
                return cand
        found = self._which(name)
        if found and (os.path.isabs(found) or os.path.isfile(found)):
            return found
        return None

    def _active_window(self) -> FocusWindow | None:
        backend = self._backend()
        if backend == "hypr":
            return self._hypr_active()
        if backend == "sway":
            return self._sway_active()
        return None

    def _hypr_active(self) -> FocusWindow | None:
        hypr = self._bin("hyprctl", _HYPR_CANDIDATES)
        if not hypr:
            return None
        try:
            proc = self._run([hypr, "activewindow", "-j"], timeout=2.0)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        # empty object when no window
        if not data or data.get("address") in (None, "0x0", "0"):
            if not data.get("class") and not data.get("title"):
                return None
        app = str(data.get("class") or data.get("initialClass") or "unknown")
        title = str(data.get("title") or "")
        pid_raw = data.get("pid")
        pid = int(pid_raw) if isinstance(pid_raw, int) else None
        return FocusWindow(app=app, title=title, active=True, role="frame", pid=pid)

    def _sway_active(self) -> FocusWindow | None:
        sway = self._bin("swaymsg", _SWAY_CANDIDATES)
        if not sway:
            return None
        try:
            proc = self._run([sway, "-t", "get_tree"], timeout=2.0)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        try:
            tree = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return None
        node = _find_focused(tree)
        if node is None:
            return None
        app = str(
            node.get("app_id")
            or (node.get("window_properties") or {}).get("class")
            or node.get("name")
            or "unknown"
        )
        title = str(
            node.get("name")
            or (node.get("window_properties") or {}).get("title")
            or ""
        )
        pid_raw = node.get("pid")
        pid = int(pid_raw) if isinstance(pid_raw, int) else None
        return FocusWindow(app=app, title=title, active=True, role="frame", pid=pid)


def _find_focused(node: Any) -> dict[str, Any] | None:
    if not isinstance(node, dict):
        return None
    if node.get("focused") is True and node.get("type") in {
        "con",
        "floating_con",
        None,
        "workspace",
    }:
        # prefer leaf-ish: has app_id or window
        if node.get("app_id") or node.get("window") or node.get("pid"):
            return node
    for key in ("nodes", "floating_nodes"):
        children = node.get(key)
        if isinstance(children, list):
            for child in children:
                found = _find_focused(child)
                if found is not None:
                    return found
    # focused container without app (e.g. empty workspace) — still walk done
    if node.get("focused") is True and (
        node.get("app_id") or node.get("window") or node.get("name")
    ):
        return node
    return None
