"""Probe selection env matrices — no real DE (#46)."""

from __future__ import annotations

import pytest

from roxabi_sense.collectors.focus.probes.kde import KdeFocusProbe
from roxabi_sense.collectors.focus.select import candidate_sources, select_probe
from roxabi_sense.collectors.focus.session import SessionInfo, detect_session


@pytest.mark.parametrize(
    ("env", "session_type", "family", "order"),
    [
        (
            {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "COSMIC"},
            "wayland",
            "cosmic",
            ["atspi", "x11", "noop"],
        ),
        (
            {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "GNOME"},
            "wayland",
            "gnome",
            ["atspi", "x11", "noop"],
        ),
        (
            {"XDG_SESSION_TYPE": "x11", "XDG_CURRENT_DESKTOP": "i3"},
            "x11",
            "unknown",
            ["x11", "atspi", "noop"],
        ),
        (
            {
                "XDG_SESSION_TYPE": "wayland",
                "XDG_CURRENT_DESKTOP": "Hyprland",
                "HYPRLAND_INSTANCE_SIGNATURE": "abc",
            },
            "wayland",
            "wlroots",
            ["wlr", "atspi", "x11", "noop"],
        ),
        (
            {
                "XDG_SESSION_TYPE": "wayland",
                "XDG_CURRENT_DESKTOP": "sway",
                "SWAYSOCK": "/run/user/1000/sway-ipc.sock",
            },
            "wayland",
            "wlroots",
            ["wlr", "atspi", "x11", "noop"],
        ),
        (
            {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "KDE"},
            "wayland",
            "kde",
            ["atspi", "kde", "x11", "noop"],
        ),
        (
            {},
            "unknown",
            "unknown",
            ["atspi", "x11", "noop"],
        ),
    ],
)
def test_env_matrix_order(
    env: dict[str, str],
    session_type: str,
    family: str,
    order: list[str],
) -> None:
    info = detect_session(env)
    assert info.session_type == session_type
    assert info.desktop_family == family
    assert candidate_sources(info) == order


def test_first_ok_wins_with_fake_probes() -> None:
    class Fail:
        source = "atspi"

        def probe(self) -> bool:
            return False

        def get_active(self) -> list:
            return []

        def get_desktop(self) -> list:
            return []

    class Ok:
        source = "wlr"

        def probe(self) -> bool:
            return True

        def get_active(self) -> list:
            return []

        def get_desktop(self) -> list:
            return []

    p = select_probe(factories=[lambda: Fail(), lambda: Ok()])
    assert p.source == "wlr"


def test_all_fail_noop() -> None:
    class Fail:
        source = "x11"

        def probe(self) -> bool:
            return False

        def get_active(self) -> list:
            return []

        def get_desktop(self) -> list:
            return []

    p = select_probe(factories=[lambda: Fail(), lambda: Fail()])
    assert p.source == "noop"


def test_kde_stub_never_selected() -> None:
    assert KdeFocusProbe().probe() is False
    info = SessionInfo(session_type="wayland", desktop_family="kde", desktop_raw="KDE")
    assert "kde" in candidate_sources(info)
