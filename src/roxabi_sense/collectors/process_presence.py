"""Process presence for configured app names (pgrep-class)."""

from __future__ import annotations

import json
import subprocess
from typing import Any

from roxabi_sense.store import Store

KIND = "process"


class ProcessPresenceCollector:
    name = "process_presence"

    def __init__(self, names: tuple[str, ...]) -> None:
        self.names = names
        self._last: str | None = None

    def tick(self, store: Store) -> int:
        snapshot = self._scan()
        fingerprint = json.dumps(snapshot, sort_keys=True)
        if fingerprint == self._last:
            return 0
        self._last = fingerprint
        store.append(KIND + "_snapshot", {"processes": snapshot})
        for name, info in snapshot.items():
            store.append(
                KIND,
                {"name": name, "running": info["running"], "pids": info["pids"][:8]},
            )
        return 1 + len(snapshot)

    def _scan(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for name in self.names:
            pids = self._pgrep(name)
            out[name] = {"running": bool(pids), "pids": pids}
        return out

    @staticmethod
    def _pgrep(name: str) -> list[int]:
        try:
            proc = subprocess.run(
                ["pgrep", "-if", name],
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
