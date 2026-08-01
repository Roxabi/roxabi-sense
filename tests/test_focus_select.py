"""Session detect + ordered probe candidates."""

from __future__ import annotations

from roxabi_sense.collectors.focus.select import candidate_sources, select_probe
from roxabi_sense.collectors.focus.session import detect_session


def test_detect_cosmic_wayland() -> None:
    info = detect_session(
        {
            "XDG_SESSION_TYPE": "wayland",
            "XDG_CURRENT_DESKTOP": "COSMIC",
        }
    )
    assert info.session_type == "wayland"
    assert info.desktop_family == "cosmic"
    assert candidate_sources(info) == ["atspi", "x11", "noop"]


def test_detect_pure_x11() -> None:
    info = detect_session({"XDG_SESSION_TYPE": "x11", "XDG_CURRENT_DESKTOP": "i3"})
    assert info.session_type == "x11"
    assert candidate_sources(info) == ["x11", "atspi", "noop"]


def test_detect_hyprland() -> None:
    info = detect_session(
        {
            "XDG_SESSION_TYPE": "wayland",
            "XDG_CURRENT_DESKTOP": "Hyprland",
            "HYPRLAND_INSTANCE_SIGNATURE": "abc",
        }
    )
    assert info.desktop_family == "wlroots"
    assert candidate_sources(info) == ["wlr", "atspi", "x11", "noop"]


def test_detect_gnome_and_unknown() -> None:
    gnome = detect_session(
        {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "GNOME"}
    )
    assert gnome.desktop_family == "gnome"
    assert candidate_sources(gnome) == ["atspi", "x11", "noop"]
    unk = detect_session({})
    assert unk.session_type == "unknown"
    assert candidate_sources(unk) == ["atspi", "x11", "noop"]


def test_select_first_ok_factory() -> None:

    class Ok:
        source: str = "x11"

        def probe(self) -> bool:
            return True

        def get_active(self) -> list:
            return []

        def get_desktop(self) -> list:
            return []

    class Fail:
        source: str = "atspi"

        def probe(self) -> bool:
            return False

        def get_active(self) -> list:
            return []

        def get_desktop(self) -> list:
            return []

    p = select_probe(factories=[lambda: Fail(), lambda: Ok()])
    assert p.source == "x11"
