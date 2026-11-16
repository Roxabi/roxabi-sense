from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from roxabi_sense.store import Store


def test_immediate_batch_reserves_writer_before_gate_read(tmp_path: Path) -> None:
    db = tmp_path / "sense.db"
    with Store(db) as decision, Store(db) as contender:
        contender._conn.execute("PRAGMA busy_timeout=0")
        decision.set_meta("source_consent", "granted")

        with decision.batch(immediate=True):
            assert decision.get_meta("source_consent") == "granted"
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                with contender.batch(immediate=True):
                    contender.set_meta("source_consent", "revoked")
                    contender.append("consent.changed", {"consent": "revoked"})

            assert decision.get_meta("source_consent") == "granted"
            assert decision.last_by_kind("consent.changed") is None
            decision.append("source.accepted", {"consent": "granted"})

        assert contender.get_meta("source_consent") == "granted"
        accepted = contender.last_by_kind("source.accepted")
        assert accepted is not None
        assert accepted.payload == {"consent": "granted"}
        assert contender.last_by_kind("consent.changed") is None

        event_id = contender.append("consent.changed", {"consent": "revoked"})
        changed = decision.last_by_kind("consent.changed")
        assert changed is not None
        assert changed.id == event_id
        assert changed.payload == {"consent": "revoked"}
