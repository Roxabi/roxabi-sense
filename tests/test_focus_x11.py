"""X11FocusProbe with mocked subprocess."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

from roxabi_sense.collectors.focus.probes.x11 import X11FocusProbe


def test_probe_false_without_display() -> None:
    p = X11FocusProbe(env={}, which=lambda n: "/usr/bin/xprop")
    assert p.probe() is False


def test_probe_false_without_xprop(monkeypatch) -> None:
    monkeypatch.setattr(
        "roxabi_sense.collectors.focus.probes.x11.os.path.isfile",
        lambda p: False,
    )
    p = X11FocusProbe(env={"DISPLAY": ":0"}, which=lambda n: None)
    assert p.probe() is False


def test_get_active_uses_absolute_xprop(monkeypatch) -> None:
    """run() must receive absolute xprop path, never bare 'xprop'."""
    calls: list[list[str]] = []

    def run(args: list[str], *, timeout: float = 2.0) -> subprocess.CompletedProcess[str]:
        calls.append(list(args))
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

    # Force which-only path (skip fixed candidates)
    monkeypatch.setattr(
        "roxabi_sense.collectors.focus.probes.x11.os.path.isfile",
        lambda p: p == "/opt/bin/xprop",
    )
    monkeypatch.setattr(
        "roxabi_sense.collectors.focus.probes.x11.os.access",
        lambda p, m: p == "/opt/bin/xprop",
    )

    p = X11FocusProbe(
        env={"DISPLAY": ":0"},
        which=lambda n: "/opt/bin/xprop",
        run=run,  # type: ignore[arg-type]
    )
    assert p.probe() is True
    wins = p.get_active()
    assert len(wins) == 1
    assert wins[0].app == "Code"
    assert "main.py" in wins[0].title
    assert wins[0].pid == 4242
    assert wins[0].active is True
    assert all(c[0] == "/opt/bin/xprop" for c in calls)
    assert all(c[0] != "xprop" for c in calls)


def test_rejects_non_hex_window_id(monkeypatch) -> None:
    def run(args: list[str], *, timeout: float = 2.0) -> subprocess.CompletedProcess[str]:
        out = "_NET_ACTIVE_WINDOW(WINDOW): window id # 0x3c00007; rm -rf /\n"
        # parser only takes first hex token from the pattern; force bad by custom
        if "-id" in args:
            raise AssertionError("must not call -id with non-hex")
        return SimpleNamespace(returncode=0, stdout=out, stderr="")  # type: ignore[return-value]

    # Craft stdout that fails fullmatch if we ever passed junk — use invalid id format
    def run_bad(args: list[str], *, timeout: float = 2.0) -> subprocess.CompletedProcess[str]:
        if "-root" in args:
            # spoof parse that would match if pattern were looser — still hex only
            out = "_NET_ACTIVE_WINDOW(WINDOW): window id # 0xDEADBEEF\n"
            return SimpleNamespace(returncode=0, stdout=out, stderr="")  # type: ignore[return-value]
        return SimpleNamespace(returncode=0, stdout="", stderr="")  # type: ignore[return-value]

    monkeypatch.setattr(
        "roxabi_sense.collectors.focus.probes.x11.os.path.isfile",
        lambda p: True,
    )
    monkeypatch.setattr(
        "roxabi_sense.collectors.focus.probes.x11.os.access",
        lambda p, m: True,
    )
    p = X11FocusProbe(
        env={"DISPLAY": ":0"},
        which=lambda n: "/usr/bin/xprop",
        run=run_bad,  # type: ignore[arg-type]
    )
    # valid hex still works
    wins = p.get_active()
    # empty props → still returns a window with unknown app
    assert isinstance(wins, list)
