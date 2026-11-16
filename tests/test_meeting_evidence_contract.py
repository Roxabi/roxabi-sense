"""Consumer contract for meeting participation evidence."""

from __future__ import annotations

import time
from pathlib import Path

from pytest import MonkeyPatch

from roxabi_sense.query import SenseQuery
from roxabi_sense.store import Store


def test_title_only_meet_window_does_not_count_as_observed_meeting(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    db = tmp_path / "sense.db"
    title = "Meet – qwb-fxnf-dje - Camera and microphone recording - Google Chrome"
    with Store(db) as store:
        store.append(
            "desktop_snapshot",
            {
                "windows": [
                    {"app": "Google Chrome", "title": title, "active": True},
                    {"app": "ghostty", "title": "work", "active": False},
                ],
                "inventory": "full",
                "source": "atspi",
            },
            ts="2026-07-31T09:00:00Z",
        )
        store.append(
            "focus",
            {"app": "Google Chrome", "title": title},
            ts="2026-07-31T09:00:00Z",
        )
        store.append(
            "desktop_snapshot",
            {
                "windows": [
                    {"app": "ghostty", "title": "work", "active": True},
                    {"app": "slack", "title": "messages", "active": False},
                ],
                "inventory": "full",
                "source": "atspi",
            },
            ts="2026-07-31T09:05:00Z",
        )

    try:
        with monkeypatch.context() as env:
            env.setenv("TZ", "UTC")
            time.tzset()
            recap = SenseQuery(
                db_path=db, offline_threshold_s=120, idle_threshold_s=300
            ).day_recap("2026-07-31")
    finally:
        time.tzset()

    assert recap["meeting_total_s"] == 0
