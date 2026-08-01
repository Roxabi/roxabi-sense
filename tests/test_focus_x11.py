"""X11FocusProbe with mocked subprocess."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

from roxabi_sense.collectors.focus.probes.x11 import X11FocusProbe


def test_probe_false_without_display() -> None:
    p = X11FocusProbe(env={}, which=lambda n: "/usr/bin/xprop")
    assert p.probe() is False


def test_probe_false_without_xprop() -> None:
    p = X11FocusProbe(env={"DISPLAY": ":0"}, which=lambda n: None)
    assert p.probe() is False


def test_get_active_parses_xprop() -> None:
    def run(args: list[str], *, timeout: float = 2.0) -> subprocess.CompletedProcess[str]:
        cmd = " ".join(args)
        if "_NET_ACTIVE_WINDOW" in cmd and "-root" in cmd:
            out = "_NET_ACTIVE_WINDOW(WINDOW): window id # 0x3c00007\n"
        else:
            out = (
                'WM_CLASS(STRING) = "code", "Code"\n'
                '_NET_WM_NAME(UTF8_STRING) = "main.py — roxabi"\n'
                "_NET_WM_PID(CARDINAL) = 4242\n"
            )
        return SimpleNamespace(returncode=0, stdout=out, stderr="")  # type: ignore[return-value]

    p = X11FocusProbe(
        env={"DISPLAY": ":0"},
        which=lambda n: "/usr/bin/xprop",
        run=run,  # type: ignore[arg-type]
    )
    assert p.probe() is True
    wins = p.get_active()
    assert len(wins) == 1
    assert wins[0].app == "Code"
    assert "main.py" in wins[0].title
    assert wins[0].pid == 4242
    assert wins[0].active is True
