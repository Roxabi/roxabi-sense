"""Media presence via playerctl / MPRIS (Spotify, browser players)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from roxabi_sense.store import Store

KIND = "media"
_PLAYERCTL_CANDIDATES = (
    "/usr/bin/playerctl",
    "/usr/local/bin/playerctl",
)


class MprisCollector:
    name = "mpris"

    def __init__(self) -> None:
        self._last: str | None = None
        self._playerctl = self._resolve_playerctl()

    @staticmethod
    def _resolve_playerctl() -> str | None:
        for p in _PLAYERCTL_CANDIDATES:
            if Path(p).is_file():
                return p
        return None

    def tick(self, store: Store) -> int:
        if not self._playerctl:
            return 0
        players = self._list_players()
        snapshot: list[dict[str, Any]] = []
        for player in players:
            meta = self._metadata(player)
            if meta:
                snapshot.append(meta)
        fingerprint = json.dumps(snapshot, sort_keys=True)
        if fingerprint == self._last:
            return 0
        self._last = fingerprint
        store.append(KIND + "_snapshot", {"players": snapshot})
        return 1

    def _list_players(self) -> list[str]:
        assert self._playerctl
        try:
            proc = subprocess.run(
                [self._playerctl, "-l"],
                capture_output=True,
                text=True,
                check=False,
                timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        return [line.strip() for line in proc.stdout.splitlines() if line.strip()]

    def _metadata(self, player: str) -> dict[str, Any] | None:
        assert self._playerctl
        fmt = "{{status}}|{{playerName}}|{{artist}}|{{title}}|{{mpris:trackid}}"
        try:
            proc = subprocess.run(
                [self._playerctl, "-p", player, "metadata", "--format", fmt],
                capture_output=True,
                text=True,
                check=False,
                timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        line = proc.stdout.strip()
        if not line or proc.returncode != 0:
            return None
        parts = line.split("|", 4)
        while len(parts) < 5:
            parts.append("")
        status, name, artist, title, track_id = parts
        return {
            "player": name or player,
            "status": status,
            "artist": artist,
            "title": title,
            "track_id": track_id,
        }
