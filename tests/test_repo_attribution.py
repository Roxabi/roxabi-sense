"""Repo attribution: human focus (terminal × Herdr focused pane) vs agent progress."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from roxabi_sense.report.care import compile_care_brief
from roxabi_sense.report.day import compile_day_recap
from roxabi_sense.store import Store
from roxabi_sense.util.time import parse_ts, to_z

ALPHA = "/home/u/projects/org/alpha"
BETA = "/home/u/projects/org/beta"
DAY = "2026-07-30"


def _herdr(store: Store, ts: str, *, focused: str, beta: str = "working") -> None:
    store.append(
        "herdr_snapshot",
        {
            "count": 2,
            "panes": [
                {
                    "pane_id": "a",
                    "cwd": ALPHA,
                    "agent": "omp",
                    "status": "idle",
                    "focused": False,
                    "session_id": "s-alpha",
                    "title": "Plan alpha",
                },
                {
                    "pane_id": "b",
                    "cwd": BETA,
                    "agent": "omp",
                    "status": beta,
                    "focused": False,
                    "session_id": "s-beta",
                    "title": "Ship beta",
                },
            ],
            "focused": {"pane_id": "shell", "cwd": focused},
        },
        ts=ts,
    )


def _desktop(store: Store, minutes: range, app: str) -> None:
    # Dense activity so degraded-away gaps do not cut the dwell under test.
    for m in minutes:
        store.append(
            "desktop_snapshot",
            {"windows": [], "focus": {"app": app}},
            ts=f"2026-07-30T10:{m:02d}:00Z",
        )


def _ghostty(store: Store, ts: str, **agent: Any) -> None:
    store.append("focus", {"app": "ghostty", "title": "host: stale-label", "agent": agent}, ts=ts)


def _recap(store: Store, hour: int, minute: int) -> Any:
    return compile_day_recap(store, DAY, now=datetime(2026, 7, 30, hour, minute, tzinfo=UTC))


def test_focus_follows_herdr_focused_pane_not_comm_cwd(tmp_path: Path) -> None:
    """#79: the focus link guessed BETA via bare comm; Herdr says ALPHA is in front."""
    with Store(tmp_path / "s.db") as store:
        _herdr(store, "2026-07-30T09:59:00Z", focused=ALPHA)
        _ghostty(store, "2026-07-30T10:00:00Z", agent="omp", cwd=BETA, match="comm")
        _desktop(store, range(2, 10, 2), "ghostty")
        store.append("focus", {"app": "Google Chrome", "title": "docs"}, ts="2026-07-30T10:10:00Z")
        _desktop(store, range(12, 20, 2), "Google Chrome")
        for ts in ("2026-07-30T10:04:00Z", "2026-07-30T10:09:00Z", "2026-07-30T10:14:00Z"):
            _herdr(store, ts, focused=ALPHA)
        recap = _recap(store, 10, 20)

    assert [repo for repo, _ in recap.time_by_repo] == ["org/alpha"]
    assert recap.time_by_repo[0][1] == 600.0
    agents = {a.repo: a for a in recap.agent_time_by_repo}
    # BETA worked 09:59→10:20 while you never looked at it.
    assert agents["org/beta"].working_s == 21 * 60
    assert agents["org/beta"].unfocused_s == 21 * 60
    assert agents["org/beta"].now == {"working": 1}
    # ALPHA's agent sat idle: no progress time, still visible as current state.
    assert agents["org/alpha"].working_s == 0.0
    assert agents["org/alpha"].now == {"idle": 1}
    assert recap.focused_repo_now == "org/alpha"


def test_workspace_switch_inside_one_ghostty_window_splits_focus(tmp_path: Path) -> None:
    """No new focus event (title unchanged); the Herdr snapshot alone moves attention."""
    with Store(tmp_path / "s.db") as store:
        _herdr(store, "2026-07-30T10:00:00Z", focused=ALPHA)
        _ghostty(store, "2026-07-30T10:00:00Z")
        _desktop(store, range(2, 20, 2), "ghostty")
        _herdr(store, "2026-07-30T10:06:00Z", focused=BETA)
        _herdr(store, "2026-07-30T10:11:00Z", focused=BETA)
        _herdr(store, "2026-07-30T10:16:00Z", focused=BETA)
        recap = _recap(store, 10, 20)

    assert dict(recap.time_by_repo) == {"org/beta": 14 * 60.0, "org/alpha": 6 * 60.0}
    beta = next(a for a in recap.agent_time_by_repo if a.repo == "org/beta")
    assert beta.working_s == 20 * 60
    # Only the 6 minutes spent on ALPHA count as "agent ran without you".
    assert beta.unfocused_s == 6 * 60

    brief = compile_care_brief(
        recap, {"state": "active"}, now=datetime(2026, 7, 30, 10, 20, tzinfo=UTC)
    )
    assert brief["current_stretch"]["repo"] == "org/beta"
    assert brief["focus_repos"][0] == {"repo": "org/beta", "minutes": 14.0, "share": 0.7}
    assert brief["agent_repos"][0] == {
        "repo": "org/beta",
        "working_minutes": 20.0,
        "unfocused_minutes": 6.0,
        "now": {"working": 1},
        "sessions": [
            {
                "session_id": "s-beta",
                "title": "Ship beta",
                "working_minutes": 20.0,
                "now": "working",
            }
        ],
    }
    # Idle session: no progress, but its id / title / live status stay visible.
    assert brief["agent_repos"][1]["sessions"] == [
        {"session_id": "s-alpha", "title": "Plan alpha", "working_minutes": 0.0, "now": "idle"}
    ]


def test_suspend_hole_adds_at_most_max_hold_of_agent_time(tmp_path: Path) -> None:
    """Keyframes every 5 min: a 4 h hole is the machine asleep, capped at 10 min."""
    with Store(tmp_path / "s.db") as store:
        _herdr(store, "2026-07-30T10:00:00Z", focused=ALPHA)
        _herdr(store, "2026-07-30T14:00:00Z", focused=ALPHA)
        recap = _recap(store, 14, 5)

    beta = next(a for a in recap.agent_time_by_repo if a.repo == "org/beta")
    assert beta.working_s == 10 * 60 + 5 * 60


def test_prior_day_snapshot_is_clipped_at_midnight_and_expires(tmp_path: Path) -> None:
    with Store(tmp_path / "s.db") as store:
        start = parse_ts(store.day_bounds(DAY)[0])
        _herdr(store, to_z(start - timedelta(minutes=5)), focused=ALPHA)
        fresh = compile_day_recap(store, DAY, now=start + timedelta(minutes=30))
    with Store(tmp_path / "old.db") as store:
        _herdr(store, to_z(start - timedelta(minutes=20)), focused=ALPHA)
        stale = compile_day_recap(store, DAY, now=start + timedelta(minutes=30))

    # 23:55 snapshot held 10 min: only 00:00→00:05 belongs to this day.
    beta = next(a for a in fresh.agent_time_by_repo if a.repo == "org/beta")
    assert beta.working_s == 5 * 60
    # 23:40 snapshot expired before midnight: nothing carried in, nothing "now".
    assert stale.agent_time_by_repo == []


def test_concurrent_sessions_count_repo_wall_time_once_and_blocked_is_not_work(
    tmp_path: Path,
) -> None:
    panes = [
        {"pane_id": "b1", "cwd": BETA, "status": "working", "session_id": "s1", "title": "A"},
        {"pane_id": "b2", "cwd": BETA, "status": "working", "session_id": "s2", "title": "B"},
        {"pane_id": "a1", "cwd": ALPHA, "status": "blocked", "session_id": "s3", "title": "C"},
    ]
    with Store(tmp_path / "s.db") as store:
        store.append("herdr_snapshot", {"count": 3, "panes": panes}, ts="2026-07-30T10:00:00Z")
        recap = _recap(store, 10, 10)

    agents = {a.repo: a for a in recap.agent_time_by_repo}
    assert agents["org/beta"].working_s == 10 * 60  # wall time, not 2 × 10
    assert [s.working_s for s in agents["org/beta"].sessions] == [600.0, 600.0]
    assert agents["org/alpha"].working_s == 0.0
    assert agents["org/alpha"].now == {"blocked": 1}


def test_herdr_down_keyframes_fall_back_to_linked_cwd(tmp_path: Path) -> None:
    """Herdr installed but its server down: empty snapshots must not erase focus."""
    with Store(tmp_path / "s.db") as store:
        _ghostty(store, "2026-07-30T10:00:00Z", agent="grok", cwd=ALPHA, match="tmux_cwd")
        _desktop(store, range(2, 10, 2), "ghostty")
        for ts in ("2026-07-30T10:00:00Z", "2026-07-30T10:05:00Z"):
            store.append("herdr_snapshot", {"count": 0, "panes": []}, ts=ts)
        store.append("focus", {"app": "Google Chrome", "title": "x"}, ts="2026-07-30T10:10:00Z")
        recap = _recap(store, 10, 12)

    assert recap.time_by_repo == [("org/alpha", 600.0)]


def test_without_herdr_falls_back_to_linked_cwd_but_never_bare_comm(tmp_path: Path) -> None:
    with Store(tmp_path / "s.db") as store:
        _ghostty(store, "2026-07-30T10:00:00Z", agent="grok", cwd=ALPHA, match="tmux_cwd")
        _desktop(store, range(2, 10, 2), "ghostty")
        store.append(
            "focus",
            {"app": "ghostty", "title": "other", "agent": {"cwd": BETA, "match": "comm"}},
            ts="2026-07-30T10:05:00Z",
        )
        store.append("focus", {"app": "Google Chrome", "title": "x"}, ts="2026-07-30T10:10:00Z")
        recap = _recap(store, 10, 12)

    assert recap.time_by_repo == [("org/alpha", 300.0)]
    assert recap.agent_time_by_repo == []
