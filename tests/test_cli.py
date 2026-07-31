from __future__ import annotations

from pathlib import Path

from roxabi_sense.cli import main
from roxabi_sense.store import Store


def test_version_flag() -> None:
    try:
        main(["--version"])
    except SystemExit as exc:
        assert exc.code == 0


def test_status_missing_db(tmp_path: Path, monkeypatch, capsys) -> None:
    missing = tmp_path / "missing.db"
    monkeypatch.setenv("SENSE_DB", str(missing))
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "db: missing" in out
    assert "sense once" in out
    assert not missing.exists()


def test_day_missing_db(tmp_path: Path, monkeypatch, capsys) -> None:
    missing = tmp_path / "missing.db"
    monkeypatch.setenv("SENSE_DB", str(missing))
    assert main(["day"]) == 1
    err = capsys.readouterr().err
    assert "db: missing" in err


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


def test_day_invalid_date(tmp_path: Path, monkeypatch, capsys) -> None:
    db = tmp_path / "sense.db"
    monkeypatch.setenv("SENSE_DB", str(db))
    Store(db).close()
    assert main(["day", "--date", "nope"]) == 2
    assert "invalid day" in capsys.readouterr().err


def test_mcp_not_implemented() -> None:
    assert main(["mcp"]) == 2


def test_bad_config_exits_2(tmp_path: Path, monkeypatch, capsys) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text("[[[not valid", encoding="utf-8")
    monkeypatch.setenv("SENSE_DB", str(tmp_path / "x.db"))
    assert main(["--config", str(bad), "status"]) == 2
    assert "invalid config" in capsys.readouterr().err
