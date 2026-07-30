"""tmux session / window / pane snapshot (Ghostty → tmux → grok path)."""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

from roxabi_sense.store import Store

KIND = "tmux"


class TmuxSessionsCollector:
    name = "tmux"

    def __init__(self) -> None:
        self._last: str | None = None
        self._tmux = shutil.which("tmux")

    def tick(self, store: Store) -> int:
        if not self._tmux:
            return 0
        panes = self._list_panes()
        fingerprint = json.dumps(panes, sort_keys=True)
        if fingerprint == self._last:
            return 0
        self._last = fingerprint
        store.append(KIND + "_snapshot", {"panes": panes, "count": len(panes)})
        return 1

    def _list_panes(self) -> list[dict[str, Any]]:
        assert self._tmux
        fmt = (
            "#{session_name}\t#{window_index}\t#{window_name}\t"
            "#{pane_current_command}\t#{pane_current_path}\t#{session_attached}"
        )
        try:
            proc = subprocess.run(
                [self._tmux, "list-panes", "-a", "-F", fmt],
                capture_output=True,
                text=True,
                check=False,
                timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        if proc.returncode != 0:
            return []
        panes: list[dict[str, Any]] = []
        for line in proc.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 6:
                continue
            session, win_i, win_name, cmd, path, attached = parts[:6]
            panes.append(
                {
                    "session": session,
                    "window": win_i,
                    "window_name": win_name,
                    "command": cmd,
                    "path": path,
                    "attached": attached == "1",
                }
            )
        return panes
