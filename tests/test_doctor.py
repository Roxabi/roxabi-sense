"""sense doctor readiness checks."""

from __future__ import annotations

from pathlib import Path

from roxabi_sense.config import SenseConfig
from roxabi_sense.doctor import doctor_exit_code, run_doctor
from roxabi_sense.store import Store
from roxabi_sense.surfaces.cli import main


def test_doctor_fails_without_db(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SENSE_DB", str(tmp_path / "missing.db"))
    cfg = SenseConfig(db_path=tmp_path / "missing.db")
    report = run_doctor(cfg)
    assert report.ok is False
    assert doctor_exit_code(report) == 1
    names = {c.name: c for c in report.checks}
    assert names["db"].status == "fail"
    assert names["presence"].status == "fail"


def test_doctor_ok_with_fresh_store(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "sense.db"
    monkeypatch.setenv("SENSE_DB", str(db))
    with Store(db) as store:
        store.append("idle", {"idle": False, "source": "test"})
        from datetime import UTC, datetime

        tick = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        store.set_meta("last_tick", tick)
    cfg = SenseConfig(db_path=db, offline_threshold_s=600.0)
    report = run_doctor(cfg)
    names = {c.name: c.status for c in report.checks}
    assert names["db"] == "ok"
    assert names["last_tick"] == "ok"
    assert names["presence"] == "ok"
    # mcp may fail if extra not in this env — not required for store health
    assert doctor_exit_code(report) in {0, 1}


def test_doctor_cli_json(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("SENSE_DB", str(tmp_path / "no.db"))
    code = main(["doctor", "--json"])
    assert code == 1
    out = capsys.readouterr().out
    assert '"ok": false' in out or '"ok":false' in out
    assert "checks" in out


def test_doctor_capabilities_json(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "sense.db"
    monkeypatch.setenv("SENSE_DB", str(db))
    with Store(db) as store:
        store.append("idle", {"idle": False, "source": "test"})
        from datetime import UTC, datetime

        tick = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        store.set_meta("last_tick", tick)
        store.set_meta("focus_backend", "x11")
        store.set_meta("focus_status", "degraded")
        store.set_meta("session_type", "wayland")
        store.set_meta("desktop_family", "cosmic")
        store.set_meta("idle_watch", "ready")
    cfg = SenseConfig(db_path=db, offline_threshold_s=600.0)
    report = run_doctor(cfg)
    assert report.capabilities is not None
    assert report.capabilities["focus"].backend == "x11"
    assert report.capabilities["focus"].status == "degraded"
    assert report.capabilities["idle"].backend == "wayland-idle"
    d = report.to_dict()
    assert "capabilities" in d
    assert d["capabilities"]["focus"]["backend"] == "x11"
    names = {c.name: c for c in report.checks}
    assert "focus" in names
    assert names["focus"].status == "warn"
