"""Process presence for configured app names (safe pgrep patterns)."""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any

from roxabi_sense.store import Store

KIND = "process"
# Exact process name tokens only (no regex metachar / option injection).
_NAME_RE = re.compile(r"^[A-Za-z0-9_.,+-]{1,64}$")


class ProcessPresenceCollector:
    name = "process_presence"

    def __init__(self, names: tuple[str, ...]) -> None:
        self.names = tuple(n for n in names if _NAME_RE.match(n) and not n.startswith("-"))
        self._last: str | None = None

    def tick(self, store: Store) -> int:
        snapshot = self._scan()
        fingerprint = json.dumps(snapshot, sort_keys=True)
        if fingerprint == self._last:
            return 0
        self._last = fingerprint
        # Snapshot-only (status/day read process_snapshot).
        store.append(KIND + "_snapshot", {"processes": snapshot})
        return 1

    def _scan(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for name in self.names:
            pids = self._pgrep(name)
            out[name] = {"running": bool(pids), "pids": pids[:8]}
        return out

    @staticmethod
    def _pgrep(name: str) -> list[int]:
        """Match process name (comm) exactly, case-insensitive."""
        try:
            # -x exact name; -i case-insensitive; -- ends options.
            proc = subprocess.run(
                ["pgrep", "-xi", "--", name],
                capture_output=True,
                text=True,
                check=False,
                timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        pids: list[int] = []
        for line in proc.stdout.splitlines():
            line = line.strip()
            if line.isdigit():
                pids.append(int(line))
        return pids
