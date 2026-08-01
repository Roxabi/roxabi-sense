"""AtspiFocusProbe soft-fail + unexpected exception logging."""

from __future__ import annotations

from roxabi_sense.collectors.focus.probes.atspi import AtspiFocusProbe


def test_cold_probe_false_on_empty(monkeypatch) -> None:
    monkeypatch.setattr(
        "roxabi_sense.collectors.focus.probes.atspi.probe_once",
        lambda mode: [],
    )
    p = AtspiFocusProbe()
    assert p.probe() is False


def test_cold_probe_true_on_windows(monkeypatch) -> None:
    monkeypatch.setattr(
        "roxabi_sense.collectors.focus.probes.atspi.probe_once",
        lambda mode: [{"app": "a", "title": "t", "active": True, "role": "frame"}],
    )
    p = AtspiFocusProbe()
    assert p.probe() is True


def test_soft_exception_silent(monkeypatch) -> None:
    monkeypatch.setattr(
        "roxabi_sense.collectors.focus.probes.atspi.probe_once",
        lambda mode: (_ for _ in ()).throw(OSError("no a11y")),
    )
    p = AtspiFocusProbe()
    assert p.probe() is False


def test_unexpected_exception_logs(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "roxabi_sense.collectors.focus.probes.atspi.probe_once",
        lambda mode: (_ for _ in ()).throw(KeyError("weird")),
    )
    p = AtspiFocusProbe()
    assert p.probe() is False
    err = capsys.readouterr().out
    assert "cold probe failed" in err
    assert "KeyError" in err
