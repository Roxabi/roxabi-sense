"""FocusRuntime demote / recover."""

from __future__ import annotations

from pathlib import Path

from roxabi_sense.collectors.focus.collector import FocusCollector
from roxabi_sense.collectors.focus.probes.noop import NoopFocusProbe
from roxabi_sense.collectors.focus.runtime import FocusRuntime
from roxabi_sense.collectors.focus.session import SessionInfo
from roxabi_sense.store import Store


def test_demote_from_atspi_to_x11(tmp_path: Path, monkeypatch) -> None:
    store = Store(tmp_path / "s.db")
    c = FocusCollector(sessions_loader=lambda: [])
    session = SessionInfo(session_type="wayland", desktop_family="cosmic", desktop_raw="COSMIC")
    rt = FocusRuntime(c, session=session, env={"DISPLAY": ":0"})

    # Force x11 probe ok, atspi not
    class X11Ok:
        source = "x11"

        def probe(self) -> bool:
            return True

        def get_active(self) -> list:
            return []

        def get_desktop(self) -> list:
            return []

    class AtspiDead:
        source = "atspi"

        def probe(self) -> bool:
            return False

        def mark_healthy(self, healthy: bool) -> None:
            pass

        def get_active(self) -> list:
            return []

        def get_desktop(self) -> list:
            return []

    rt._probes["atspi"] = AtspiDead()  # type: ignore[assignment]
    rt._probes["x11"] = X11Ok()  # type: ignore[assignment]
    rt._probes["noop"] = NoopFocusProbe()
    rt.active = AtspiDead()  # type: ignore[assignment]
    c.set_source("atspi")

    rt.demote(store, reason="atspi_dead")
    assert store.get_meta("focus_backend") == "x11"
    assert store.get_meta("focus_status") == "degraded"
    assert c.backend_source == "x11"
    store.close()


def test_recover_to_atspi(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    c = FocusCollector(sessions_loader=lambda: [])
    session = SessionInfo(session_type="wayland", desktop_family="gnome", desktop_raw="GNOME")
    rt = FocusRuntime(c, session=session)

    class X11Ok:
        source = "x11"

        def probe(self) -> bool:
            return True

        def get_active(self) -> list:
            return []

        def get_desktop(self) -> list:
            return []

    class Atspi:
        source = "atspi"

        def __init__(self) -> None:
            self._ok = False

        def probe(self) -> bool:
            return self._ok

        def mark_healthy(self, healthy: bool) -> None:
            self._ok = healthy

        def get_active(self) -> list:
            return []

        def get_desktop(self) -> list:
            return []

    atspi = Atspi()
    rt._probes["atspi"] = atspi  # type: ignore[assignment]
    rt._probes["x11"] = X11Ok()  # type: ignore[assignment]
    rt._activate(store, X11Ok(), reason="demote")  # type: ignore[arg-type]
    assert store.get_meta("focus_backend") == "x11"
    rt.mark_atspi(store, healthy=True)
    assert store.get_meta("focus_backend") == "atspi"
    assert store.get_meta("focus_status") == "available"
    store.close()
