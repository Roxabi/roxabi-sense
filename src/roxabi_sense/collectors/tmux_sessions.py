"""tmux session / window / pane snapshot (Ghostty → tmux → grok path)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from roxabi_sense.store import Store

KIND = "tmux"
_TMUX_CANDIDATES = ("/usr/bin/tmux", "/usr/local/bin/tmux")


class TmuxSessionsCollector:
    name = "tmux"

    def __init__(self) -> None:
        self._last: str | None = None
        self._tmux = next((p for p in _TMUX_CANDIDATES if Path(p).is_file()), None)

    def tick(self, store: Store) -> int:
        if not self._tmux:
            return 0
        panes = self._list_panes()
        # Fingerprint structure only — pane_title churns with Thinking/Running.
        fingerprint = json.dumps(
            [
                {
                    k: p.get(k)
                    for k in (
                        "session",
                        "window",
                        "window_name",
                        "command",
                        "path",
                        "attached",
                    )
                }
                for p in panes
            ],
            sort_keys=True,
        )
        if fingerprint == self._last:
            return 0
        self._last = fingerprint
        store.append(KIND + "_snapshot", {"panes": panes, "count": len(panes)})
        return 1

    def _list_panes(self) -> list[dict[str, Any]]:
        assert self._tmux
        fmt = (
            "#{session_name}\t#{window_index}\t#{window_name}\t"
            "#{pane_current_command}\t#{pane_current_path}\t#{session_attached}\t"
            "#{pane_title}"
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
            pane_title = parts[6] if len(parts) > 6 else ""
            if len(parts) > 7:
                pane_title = "\t".join(parts[6:])
            panes.append(
                {
                    "session": session,
                    "window": win_i,
                    "window_name": win_name,
                    "command": cmd,
                    "path": path,
                    "attached": attached == "1",
                    "pane_title": pane_title,
                }
            )
        return panes
