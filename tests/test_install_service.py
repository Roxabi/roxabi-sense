"""install-service unit writer."""

from __future__ import annotations

from pathlib import Path

from roxabi_sense.cli import main
from roxabi_sense.install_service import write_unit


def test_write_unit_graphical_session(tmp_path: Path) -> None:
    dest = tmp_path / "roxabi-sense.service"
    write_unit(exec_start="/tmp/fake-sense", dest=dest)
    body = dest.read_text(encoding="utf-8")
    assert "WantedBy=graphical-session.target" in body
    assert "ExecStart=/tmp/fake-sense daemon" in body
    assert "Restart=always" in body
    assert "default.target" not in body.split("[Install]")[-1]


def test_install_service_cli(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home" / ".local" / "bin").mkdir(parents=True)
    sense_bin = tmp_path / "home" / ".local" / "bin" / "sense"
    sense_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    sense_bin.chmod(0o755)
    # which finds sense if PATH includes it
    monkeypatch.setenv("PATH", f"{sense_bin.parent}:{tmp_path}")

    def _fake_run(*_a, **_k):
        class _R:
            returncode = 0

        return _R()

    monkeypatch.setattr("roxabi_sense.install_service.subprocess.run", _fake_run)
    code = main(["install-service"])
    assert code == 0
    out = capsys.readouterr().out
    assert "wrote" in out
    assert "daemon-reload" in out
    unit = tmp_path / "cfg" / "systemd" / "user" / "roxabi-sense.service"
    assert unit.is_file()
    body = unit.read_text(encoding="utf-8")
    assert "WantedBy=graphical-session.target" in body
    assert "not implemented" not in out.lower()
