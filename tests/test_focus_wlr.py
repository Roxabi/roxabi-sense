"""WlrFocusProbe with mocked hyprctl / swaymsg."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

from roxabi_sense.collectors.focus.probes.wlr import WlrFocusProbe


def test_probe_false_without_compositor() -> None:
    p = WlrFocusProbe(env={}, which=lambda n: "/usr/bin/hyprctl")
    assert p.probe() is False


def test_hypr_activewindow(monkeypatch) -> None:
    payload = {"class": "kitty", "title": "shell", "pid": 42, "address": "0x1"}

    def run(args: list[str], *, timeout: float = 2.0) -> subprocess.CompletedProcess[str]:
        assert args[0] == "/usr/bin/hyprctl"
        assert "activewindow" in args
        return SimpleNamespace(  # type: ignore[return-value]
            returncode=0, stdout=json.dumps(payload), stderr=""
        )

    monkeypatch.setattr(
        "roxabi_sense.collectors.focus.probes.wlr.os.path.isfile",
        lambda p: p == "/usr/bin/hyprctl",
    )
    monkeypatch.setattr(
        "roxabi_sense.collectors.focus.probes.wlr.os.access",
        lambda p, m: True,
    )
    p = WlrFocusProbe(
        env={"HYPRLAND_INSTANCE_SIGNATURE": "sig"},
        which=lambda n: "/usr/bin/hyprctl",
        run=run,  # type: ignore[arg-type]
    )
    assert p.probe() is True
    wins = p.get_active()
    assert len(wins) == 1
    assert wins[0].app == "kitty"
    assert wins[0].title == "shell"
    assert wins[0].pid == 42
    assert wins[0].active is True


def test_sway_focused_tree(monkeypatch) -> None:
    tree = {
        "nodes": [
            {
                "type": "workspace",
                "nodes": [
                    {
                        "type": "con",
                        "focused": True,
                        "app_id": "firefox",
                        "name": "MDN",
                        "pid": 99,
                    }
                ],
            }
        ]
    }

    def run(args: list[str], *, timeout: float = 2.0) -> subprocess.CompletedProcess[str]:
        assert "get_tree" in args
        return SimpleNamespace(  # type: ignore[return-value]
            returncode=0, stdout=json.dumps(tree), stderr=""
        )

    monkeypatch.setattr(
        "roxabi_sense.collectors.focus.probes.wlr.os.path.isfile",
        lambda p: p == "/usr/bin/swaymsg",
    )
    monkeypatch.setattr(
        "roxabi_sense.collectors.focus.probes.wlr.os.access",
        lambda p, m: True,
    )
    p = WlrFocusProbe(
        env={"SWAYSOCK": "/tmp/sway.sock"},
        which=lambda n: "/usr/bin/swaymsg",
        run=run,  # type: ignore[arg-type]
    )
    assert p.probe() is True
    wins = p.get_active()
    assert wins[0].app == "firefox"
    assert wins[0].title == "MDN"
    assert wins[0].pid == 99


def test_missing_cli_probe_false(monkeypatch) -> None:
    monkeypatch.setattr(
        "roxabi_sense.collectors.focus.probes.wlr.os.path.isfile",
        lambda p: False,
    )
    p = WlrFocusProbe(
        env={"HYPRLAND_INSTANCE_SIGNATURE": "sig"},
        which=lambda n: None,
    )
    assert p.probe() is False
