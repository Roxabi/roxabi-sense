"""Attention grain: multi-Ghostty context vs AT-SPI title thrash + terminal stays."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from roxabi_sense.report.day import (
    compile_day_recap,
    format_day_recap,
    format_day_recap_share,
)
from roxabi_sense.report.segments import (
    FocusSegment,
    attention_key,
    attention_segments,
    switch_count,
    terminal_stay_stats,
)
from roxabi_sense.report.top_apps import session_shape
from roxabi_sense.store import Store


def _seg(
    *,
    app: str = "ghostty",
    title: str,
    start: str,
    end: str,
    duration_s: float,
    session_id: str | None = None,
    cwd: str | None = None,
    pid: int | None = 671849,
    agent: str | None = "grok",
    agent_pid: int | None = None,
    agent_match: str | None = None,
) -> FocusSegment:
    return FocusSegment(
        start=start,
        end=end,
        duration_s=duration_s,
        app=app,
        title=title,
        cwd=cwd,
        agent=agent,
        pid=pid,
        session_id=session_id,
        agent_pid=agent_pid,
        agent_match=agent_match,
    )


def test_attention_key_prefers_session_over_shared_window_pid() -> None:
    a = _seg(
        title="A - grok",
        start="2026-07-30T10:00:00Z",
        end="2026-07-30T10:01:00Z",
        duration_s=60,
        session_id="sess-a",
        pid=671849,
    )
    b = _seg(
        title="B - grok",
        start="2026-07-30T10:01:00Z",
        end="2026-07-30T10:02:00Z",
        duration_s=60,
        session_id="sess-b",
        pid=671849,  # same Ghostty process
    )
    assert attention_key(a) != attention_key(b)
    assert attention_key(a)[1] == "session"
    assert attention_key(a)[2] == "sess-a"


def test_title_thrash_same_session_merges() -> None:
    fine = [
        _seg(
            title=f"title-{i}",
            start=f"2026-07-30T10:00:{i:02d}Z",
            end=f"2026-07-30T10:00:{i + 1:02d}Z",
            duration_s=10,
            session_id="same",
            agent_pid=100,
            agent_match="tmux_pane_title+tmux_child_pid",
        )
        for i in range(0, 50, 10)
    ]
    # 5 segments × 10s title thrash, same session
    attn = attention_segments(fine)
    assert len(attn) == 1
    assert attn[0].duration_s == 50
    assert switch_count(fine) == 0  # same attention_key throughout
    assert switch_count(fine, key=lambda s: (s.app, s.title)) == 4
    assert switch_count(fine, key=lambda s: s.app) == 0


def test_short_ghostty_hops_are_kept() -> None:
    """3–5s answer-and-next-agent hops must count as context switches."""
    fine = [
        _seg(
            title="A - grok",
            start="2026-07-30T10:00:00Z",
            end="2026-07-30T10:00:05Z",
            duration_s=5,
            session_id="sess-a",
            agent_pid=1,
        ),
        _seg(
            title="B - grok",
            start="2026-07-30T10:00:05Z",
            end="2026-07-30T10:00:09Z",
            duration_s=4,
            session_id="sess-b",
            agent_pid=2,
        ),
        _seg(
            title="C - grok",
            start="2026-07-30T10:00:09Z",
            end="2026-07-30T10:00:14Z",
            duration_s=5,
            session_id="sess-c",
            agent_pid=3,
        ),
    ]
    attn = attention_segments(fine)
    assert len(attn) == 3
    assert max(0, len(attn) - 1) == 2


def test_two_ghostty_sessions_are_context_switches() -> None:
    fine = [
        _seg(
            title="boilerplate docs - grok",
            start="2026-07-30T10:00:00Z",
            end="2026-07-30T10:05:00Z",
            duration_s=300,
            session_id="bp",
            cwd="/home/m/projects/roxabi/roxabi-boilerplate-cf",
            agent_pid=111,
        ),
        # title thrash inside first session
        _seg(
            title="boilerplate remaining - grok",
            start="2026-07-30T10:05:00Z",
            end="2026-07-30T10:10:00Z",
            duration_s=300,
            session_id="bp",
            cwd="/home/m/projects/roxabi/roxabi-boilerplate-cf",
            agent_pid=111,
        ),
        _seg(
            title="lucy nika - grok",
            start="2026-07-30T10:10:00Z",
            end="2026-07-30T10:20:00Z",
            duration_s=600,
            session_id="gosilex",
            cwd="/home/m/projects/gosilex",
            agent_pid=222,
        ),
        _seg(
            title="slack",
            start="2026-07-30T10:20:00Z",
            end="2026-07-30T10:25:00Z",
            duration_s=300,
            app="slack",
            session_id=None,
            agent=None,
            pid=999,
            agent_pid=None,
        ),
    ]
    attn = attention_segments(fine)
    assert len(attn) == 3  # bp merged, gosilex, slack
    assert switch_count(fine, key=lambda s: s.app) == 1  # ghostty→slack only
    assert max(0, len(attn) - 1) == 2  # bp→gosilex→slack
    assert "boilerplate" in attn[0].title


def test_terminal_stay_stats_long_vs_short() -> None:
    attn = [
        _seg(
            title="hop",
            start="2026-07-30T10:00:00Z",
            end="2026-07-30T10:00:10Z",
            duration_s=10,
            session_id="a",
        ),
        _seg(
            title="deep",
            start="2026-07-30T10:00:10Z",
            end="2026-07-30T10:12:10Z",
            duration_s=720,  # 12m
            session_id="b",
        ),
        _seg(
            title="slack",
            start="2026-07-30T10:12:10Z",
            end="2026-07-30T10:15:10Z",
            duration_s=180,
            app="slack",
            session_id=None,
            agent=None,
        ),
    ]
    stats = terminal_stay_stats(attn)
    assert stats.visits == 2  # slack excluded
    assert stats.ge_2m == 1
    assert stats.ge_5m == 1
    assert stats.ge_10m == 1
    assert stats.time_ge_5m_s == 720.0
    assert stats.time_total_s == 730.0


def test_session_shape_ignores_title_thrash() -> None:
    fine: list[FocusSegment] = []
    t0 = datetime(2026, 7, 30, 10, 0, 0, tzinfo=UTC)
    for i in range(40):
        start = t0.timestamp() + i * 30
        from datetime import datetime as dt

        s = dt.fromtimestamp(start, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        e = dt.fromtimestamp(start + 30, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        fine.append(
            _seg(
                title=f"work step {i} - grok",
                start=s,
                end=e,
                duration_s=30,
                session_id="deep-session",
            )
        )
    assert session_shape(fine) == "fragmented"  # title grain
    attn = attention_segments(fine)
    assert len(attn) == 1
    assert session_shape(attn) is None
    a = attn[0]
    b = _seg(
        title="other - grok",
        start="2026-07-30T10:20:00Z",
        end="2026-07-30T10:40:00Z",
        duration_s=1200,
        session_id="other-session",
    )
    assert session_shape([a, b]) == "deep"


def test_day_recap_switch_metrics(tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    ghost_pid = 671849
    with Store(db) as store:
        for i, title in enumerate(
            ["Alpha work - grok", "Alpha renamed - grok", "Alpha thrash - grok"]
        ):
            store.append(
                "focus",
                {
                    "app": "ghostty",
                    "title": title,
                    "pid": ghost_pid,
                    "source": "atspi",
                    "agent": {
                        "agent": "grok",
                        "session_id": "sess-a",
                        "cwd": "/home/m/projects/gosilex",
                        "pid": 1001,
                        "match": "tmux_pane_title+tmux_child_pid",
                    },
                },
                ts=f"2026-07-30T10:0{i}:00Z",
            )
        store.append(
            "focus",
            {
                "app": "ghostty",
                "title": "Beta work - grok",
                "pid": ghost_pid,
                "source": "atspi",
                "agent": {
                    "agent": "grok",
                    "session_id": "sess-b",
                    "cwd": "/home/m/projects/roxabi/roxabi-boilerplate-cf",
                    "pid": 1002,
                    "match": "tmux_pane_title+tmux_child_pid",
                },
            },
            ts="2026-07-30T10:10:00Z",
        )
        store.append(
            "focus",
            {"app": "slack", "title": "int-logs", "pid": 50, "source": "atspi"},
            ts="2026-07-30T10:20:00Z",
        )
        for m in (5, 8, 12, 15, 18, 22, 25, 28):
            store.append(
                "desktop_snapshot",
                {"windows": [{"app": "ghostty", "title": "x", "active": True}]},
                ts=f"2026-07-30T10:{m:02d}:00Z",
            )
        recap = compile_day_recap(
            store,
            "2026-07-30",
            now=datetime(2026, 7, 30, 12, 0, 0, tzinfo=UTC),
        )

    assert recap.focus_switches_app == 1  # ghostty → slack
    assert recap.focus_switches_context == 2  # A → B → slack (hops kept)
    assert recap.focus_switches >= recap.focus_switches_context
    assert len(recap.attention_segments) == 3
    assert recap.terminal_stays.visits >= 1
    text = format_day_recap(recap)
    assert "focus_switches:" in text
    assert "ctx ·" in text and "title" in text
    assert "terminal_stays:" in text
    assert "ctx_raw" not in text
    assert "≥30s" not in text
    share = format_day_recap_share(recap)
    assert share.startswith("sense ·")
    assert "sw  " in share
    assert "apps  " in share
    assert len(share.splitlines()) <= 8
