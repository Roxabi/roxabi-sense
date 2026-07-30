from __future__ import annotations

from pathlib import Path

from roxabi_sense.config import SenseConfig
from roxabi_sense.daemon import build_collectors, collect_once, tick_all
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
        focus=False,
    )
    # no collectors → 0 events but last_tick still set via empty tick_all
    n = collect_once(cfg)
    assert n == 0
    store = Store(db)
    assert store.get_meta("last_tick") is not None
    store.close()


def test_build_collectors_flags() -> None:
    cfg = SenseConfig(
        agent_sessions=True,
        process_presence=False,
        idle=False,
        mpris=False,
        tmux=False,
        focus=False,
    )
    cols = build_collectors(cfg)
    assert len(cols) == 1
    assert cols[0].name == "agent_sessions"
