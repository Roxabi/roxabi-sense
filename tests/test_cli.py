from __future__ import annotations

import os
from pathlib import Path

from roxabi_sense.cli import main
from roxabi_sense.store import Store


def test_version_flag() -> None:
    try:
        main(["--version"])
    except SystemExit as exc:
        assert exc.code == 0


def test_status_missing_db(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SENSE_DB", str(tmp_path / "missing.db"))
    # load_config uses SENSE_DB via default_db_path only if no config —
    # status uses cfg.db_path from load_config which reads SENSE_DB via default
    from roxabi_sense import paths

    monkeypatch.setattr(paths, "default_db_path", lambda: tmp_path / "missing.db")
    # Also patch load_config path by setting env before import usage in main
    os.environ["SENSE_DB"] = str(tmp_path / "missing.db")
    assert main(["status"]) == 0


def test_status_and_day_with_data(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "sense.db"
    monkeypatch.setenv("SENSE_DB", str(db))
    store = Store(db)
    store.append("idle", {"idle": False, "locked": False})
    store.append(
        "agent_sessions_snapshot",
        {"count": 1, "sessions": [{"agent": "grok", "cwd": "/x"}]},
    )
    store.set_meta("last_tick", "2026-07-30T12:00:00Z")
    store.close()
    assert main(["status"]) == 0
    assert main(["day", "--json"]) == 0


def test_mcp_not_implemented() -> None:
    assert main(["mcp"]) == 2
