from __future__ import annotations

from pathlib import Path

from roxabi_sense.collectors.focus.collector import FocusCollector
from roxabi_sense.collectors.focus.ownership import acquire_focus_owner, release_focus_owner
from roxabi_sense.collectors.focus.probes.cosmic import CosmicFocusProbe
from roxabi_sense.collectors.focus.protocol import FocusWindow
from roxabi_sense.config import SenseConfig
from roxabi_sense.daemon import build_collectors, build_poll_collectors, collect_once, tick_all
from roxabi_sense.query import SenseQuery
from roxabi_sense.store import Store


class _Ok:
    name = "ok"

    def tick(self, store: Store) -> int:
        store.append("idle", {"idle": False})
        return 1


class _Boom:
    name = "boom"

    def tick(self, store: Store) -> int:
        raise RuntimeError("explode")


def test_tick_all_isolates_failures(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    n = tick_all([_Boom(), _Ok()], store)
    assert n == 1
    assert store.count() == 1
    store.close()


def test_collect_once_writes_meta(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "s.db"
    cfg = SenseConfig(
        db_path=db,
        agent_sessions=False,
        process_presence=False,
        idle=False,
        mpris=False,
        tmux=False,
        herdr=False,
        focus=False,
    )
    # no collectors → 0 events but last_tick still set via empty tick_all
    n = collect_once(cfg)
    assert n == 0
    store = Store(db)
    assert store.get_meta("last_tick") is not None
    assert store.get_meta("session_bound") in {"0", "1"}
    store.close()


def test_build_collectors_flags() -> None:
    cfg = SenseConfig(
        agent_sessions=True,
        process_presence=False,
        idle=False,
        mpris=False,
        tmux=False,
        herdr=False,
        focus=False,
    )
    cols = build_collectors(cfg)
    assert len(cols) == 1
    assert cols[0].name == "agent_sessions"


def test_poll_collectors_exclude_focus() -> None:
    cfg = SenseConfig(
        agent_sessions=True,
        process_presence=False,
        idle=False,
        mpris=False,
        tmux=False,
        herdr=False,
        focus=True,
    )
    poll = build_poll_collectors(cfg)
    assert all(c.name != "focus_atspi" for c in poll)
    all_c = build_collectors(cfg)
    assert any(c.name == "focus_atspi" for c in all_c)


def test_once_does_not_steal_live_focus_writer(tmp_path: Path, monkeypatch) -> None:
    from datetime import UTC, datetime

    db = tmp_path / "s.db"
    owner = acquire_focus_owner(db)
    assert owner is not None
    monkeypatch.setattr(
        "roxabi_sense.collectors.focus.collector.resolve_app_name", lambda app, pid: app
    )
    monkeypatch.setattr(
        "roxabi_sense.collectors.focus.collector.find_agent_link", lambda *a, **kw: None
    )
    monkeypatch.setattr("roxabi_sense.collectors.focus.collector.children_map", lambda: {})
    monkeypatch.setattr("roxabi_sense.collectors.focus.collector.list_mux_agent_panes", lambda: [])
    monkeypatch.setattr(
        "roxabi_sense.daemon.build_poll_collectors", lambda cfg, logind_idle: [_Ok()]
    )
    probe = CosmicFocusProbe(env={})
    probe.reason = "observed"
    probe.observed_at = datetime.now(UTC)
    collector = FocusCollector(focus_probe=probe, sessions_loader=lambda: [])
    window = FocusWindow(app="editor", title="", active=True, window_id="epoch:one")
    cfg = SenseConfig(db_path=db, focus=True, idle=False)
    try:
        with Store(db) as store:
            collector.apply(store, [window], mode="focus")
            first = store.last_by_kind("focus_coverage")
            before = store.get_meta("focus_observation")
            assert first is not None
            assert collect_once(cfg) == 1
            assert store.last_by_kind("focus_coverage") == first
            assert store.get_meta("focus_observation") == before
            probe.observed_at = datetime.now(UTC)
            collector.apply(store, [window], mode="focus")
            assert store.last_by_kind("focus_coverage") == first
        result = SenseQuery(db_path=db, offline_threshold_s=120, idle_threshold_s=300).active_now()
        assert result["focus_quality"] == "observed"
        assert result["focus"]["app"] == "editor"
    finally:
        release_focus_owner(owner)
