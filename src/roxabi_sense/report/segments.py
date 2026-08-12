"""Focus dwell segments; away gaps live in ``report.away`` (ADR-002).

Two grains:
- **fine** (``focus_segments``): app + title — top windows / timeline
- **attention** (``attention_segments``): context key (session/agent) —
  multitasking hops including short 3–5s agent checks; title thrash collapsed

Complementary (not primary multitask):
- **terminal stay stats** — how often / how long you *stay* on a terminal context
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import TYPE_CHECKING, Any

from roxabi_sense.report.away import IDLE_GAP_S, AwaySegment, away_segments
from roxabi_sense.store import Event
from roxabi_sense.util.time import parse_ts, to_z
from roxabi_sense.util.titles import normalize_title

if TYPE_CHECKING:
    from roxabi_sense.report.meeting_sessions import MeetingSession

# Re-export for existing importers (meeting, top_apps, tests).
__all__ = [
    "LONG_STAY_S",
    "AwaySegment",
    "FocusSegment",
    "IDLE_GAP_S",
    "MIN_DWELL_S",
    "TerminalStayStats",
    "attention_key",
    "attention_segments",
    "away_segments",
    "focus_segments",
    "horizon_dt",
    "hour_apps",
    "is_terminal_app",
    "norm_app",
    "sum_by",
    "switch_count",
    "terminal_stay_stats",
    "top_titles",
]

MIN_DWELL_S = 3.0  # ignore micro-focus flickers (fine title segments)
# Complementary “I stayed a while” thresholds (not used to drop short hops).
LONG_STAY_S = (120.0, 300.0, 600.0)  # ≥2m, ≥5m, ≥10m
_APP_ALIASES: dict[str, str] = {
    "unnamed": "ghostty",
    "xdg-desktop-portal-gtk": "dialog",
}
# Multi-window single process: window pid does not identify a surface.
_SHARED_PID_APPS: frozenset[str] = frozenset({"ghostty", "unnamed"})
_TERMINAL_APPS: frozenset[str] = frozenset({"ghostty", "unnamed"})


@dataclass(frozen=True)
class FocusSegment:
    start: str
    end: str
    duration_s: float
    app: str
    title: str
    cwd: str | None = None
    agent: str | None = None
    pid: int | None = None  # window / app process (often shared on Ghostty)
    session_id: str | None = None  # agent session when linked
    agent_pid: int | None = None  # grok/claude process under the pane
    agent_match: str | None = None  # agent_link match tier


def norm_app(raw: str | None) -> str:
    app = (raw or "?").strip() or "?"
    return _APP_ALIASES.get(app.lower(), app)


def horizon_dt(day_end_z: str, now: datetime | None) -> datetime:
    end = parse_ts(day_end_z)
    n = now or datetime.now(UTC)
    if n.tzinfo is None:
        n = n.replace(tzinfo=UTC)
    return min(n, end)


def attention_key(seg: FocusSegment) -> tuple[str, ...]:
    """Stable attention identity for multitasking metrics.

    Prefer agent session (distinguishes two Ghostty windows) over window pid
    (shared on multi-window terminals) over raw title (AT-SPI thrash).
    """
    app = seg.app
    if seg.session_id:
        return (app, "session", seg.session_id)
    if seg.agent_pid is not None:
        return (app, "agent_pid", str(seg.agent_pid))
    if seg.cwd and app.lower() in _TERMINAL_APPS:
        return (app, "cwd", seg.cwd)
    if seg.pid is not None and app.lower() not in _SHARED_PID_APPS:
        return (app, "pid", str(seg.pid))
    return (app, "app")


def switch_count(
    segments: list[FocusSegment],
    *,
    key: Any = None,
) -> int:
    """Count key changes along segments (default: full attention_key)."""
    if len(segments) < 2:
        return 0
    key_fn = key if key is not None else attention_key
    n = 0
    last = key_fn(segments[0])
    for s in segments[1:]:
        k = key_fn(s)
        if k != last:
            n += 1
            last = k
    return n


def is_terminal_app(app: str) -> bool:
    return (app or "").lower() in _TERMINAL_APPS or app == "ghostty"


def attention_segments(fine: list[FocusSegment]) -> list[FocusSegment]:
    """Merge adjacent fine segments that share the same attention_key.

    Short hops between different sessions/windows are preserved (3–5s agent
    checks count). Only same-key runs merge (title thrash within one session).

    Title becomes the longest-dwell title within the merged run (for display).
    """
    if not fine:
        return []
    out: list[FocusSegment] = []
    run_key = attention_key(fine[0])
    start = fine[0].start
    end = fine[0].end
    dur = fine[0].duration_s
    title_dwell: dict[str, float] = {fine[0].title: fine[0].duration_s}
    meta = fine[0]

    def _flush() -> None:
        nonlocal start, end, dur, title_dwell, meta
        best_title = max(title_dwell.items(), key=lambda kv: (kv[1], kv[0]))[0]
        out.append(
            replace(
                meta,
                start=start,
                end=end,
                duration_s=dur,
                title=best_title,
            )
        )

    for seg in fine[1:]:
        k = attention_key(seg)
        if k == run_key:
            end = seg.end
            dur += seg.duration_s
            title_dwell[seg.title] = title_dwell.get(seg.title, 0.0) + seg.duration_s
            meta = _prefer_meta(meta, seg)
            continue
        _flush()
        run_key = k
        start = seg.start
        end = seg.end
        dur = seg.duration_s
        title_dwell = {seg.title: seg.duration_s}
        meta = seg
    _flush()
    return out


@dataclass(frozen=True)
class TerminalStayStats:
    """Complementary: continuous terminal-context visits (attention grain).

    Answers “how often / how long do I stay on a terminal?” — not multitask hop
    count. Short hops still appear as short visits in ``visits`` / median.
    """

    visits: int
    median_s: float
    mean_s: float
    # Counts of visits meeting each threshold (a 12m visit counts in all three).
    ge_2m: int
    ge_5m: int
    ge_10m: int
    # Time spent in visits of at least that length.
    time_ge_2m_s: float
    time_ge_5m_s: float
    time_ge_10m_s: float
    time_total_s: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def terminal_stay_stats(
    attention: list[FocusSegment],
    *,
    thresholds_s: tuple[float, ...] = LONG_STAY_S,
) -> TerminalStayStats:
    """Stats over terminal attention visits only (ghostty / unnamed)."""
    t2, t5, t10 = (
        thresholds_s[0] if len(thresholds_s) > 0 else 120.0,
        thresholds_s[1] if len(thresholds_s) > 1 else 300.0,
        thresholds_s[2] if len(thresholds_s) > 2 else 600.0,
    )
    terms = [s for s in attention if is_terminal_app(s.app) and s.duration_s > 0]
    if not terms:
        return TerminalStayStats(
            visits=0,
            median_s=0.0,
            mean_s=0.0,
            ge_2m=0,
            ge_5m=0,
            ge_10m=0,
            time_ge_2m_s=0.0,
            time_ge_5m_s=0.0,
            time_ge_10m_s=0.0,
            time_total_s=0.0,
        )
    durs = [s.duration_s for s in terms]
    total = float(sum(durs))
    return TerminalStayStats(
        visits=len(terms),
        median_s=round(float(median(durs)), 1),
        mean_s=round(total / len(durs), 1),
        ge_2m=sum(1 for d in durs if d >= t2),
        ge_5m=sum(1 for d in durs if d >= t5),
        ge_10m=sum(1 for d in durs if d >= t10),
        time_ge_2m_s=round(sum(d for d in durs if d >= t2), 1),
        time_ge_5m_s=round(sum(d for d in durs if d >= t5), 1),
        time_ge_10m_s=round(sum(d for d in durs if d >= t10), 1),
        time_total_s=round(total, 1),
    )


def _prefer_meta(base: FocusSegment, other: FocusSegment) -> FocusSegment:
    """Keep first segment's identity; fill missing agent/cwd/pid from later."""
    return replace(
        base,
        cwd=base.cwd or other.cwd,
        agent=base.agent or other.agent,
        pid=base.pid if base.pid is not None else other.pid,
        session_id=base.session_id or other.session_id,
        agent_pid=base.agent_pid if base.agent_pid is not None else other.agent_pid,
        agent_match=base.agent_match or other.agent_match,
    )


def focus_segments(
    focus_events: list[Event],
    away: list[AwaySegment],
    *,
    horizon: datetime,
) -> list[FocusSegment]:
    """Attribute focus dwell (fine grain: app+title), cutting out away gaps."""
    if not focus_events:
        return []
    # (t, app, title, cwd, agent, pid, session_id, agent_match, agent_pid)
    collapsed: list[
        tuple[
            datetime,
            str,
            str,
            str | None,
            str | None,
            int | None,
            str | None,
            str | None,
            int | None,
        ]
    ] = []
    for e in focus_events:
        app = norm_app(str(e.payload.get("app") or ""))
        title = normalize_title(str(e.payload.get("title") or ""))
        ag = e.payload.get("agent") if isinstance(e.payload.get("agent"), dict) else {}
        cwd_s = str(ag["cwd"]) if isinstance(ag, dict) and ag.get("cwd") else None
        agent_s = str(ag["agent"]) if isinstance(ag, dict) and ag.get("agent") else None
        sid = (
            str(ag["session_id"])
            if isinstance(ag, dict) and ag.get("session_id")
            else None
        )
        match_s = (
            str(ag["match"]) if isinstance(ag, dict) and ag.get("match") else None
        )
        agent_pid = _as_int(ag.get("pid")) if isinstance(ag, dict) else None
        win_pid = _as_int(e.payload.get("pid"))
        t = parse_ts(e.ts)
        if collapsed and collapsed[-1][1] == app and collapsed[-1][2] == title:
            prev = collapsed[-1]
            collapsed[-1] = (
                prev[0],
                app,
                title,
                cwd_s or prev[3],
                agent_s or prev[4],
                win_pid if win_pid is not None else prev[5],
                sid or prev[6],
                match_s or prev[7],
                agent_pid if agent_pid is not None else prev[8],
            )
            continue
        collapsed.append(
            (t, app, title, cwd_s, agent_s, win_pid, sid, match_s, agent_pid)
        )

    away_ranges = [(parse_ts(a.start), parse_ts(a.end)) for a in away]
    segs: list[FocusSegment] = []
    for i, row in enumerate(collapsed):
        t0, app, title, cwd, agent, win_pid, sid, match_s, agent_pid = row
        t1 = collapsed[i + 1][0] if i + 1 < len(collapsed) else horizon
        if t1 < t0:
            t1 = t0
        for a0, a1 in _subtract_ranges(t0, t1, away_ranges):
            dur = (a1 - a0).total_seconds()
            if dur < MIN_DWELL_S:
                continue
            segs.append(
                FocusSegment(
                    start=to_z(a0),
                    end=to_z(a1),
                    duration_s=dur,
                    app=app,
                    title=title,
                    cwd=cwd,
                    agent=agent,
                    pid=win_pid,
                    session_id=sid,
                    agent_pid=agent_pid,
                    agent_match=match_s,
                )
            )
    return segs


def _as_int(raw: Any) -> int | None:
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)
    return None


def _subtract_ranges(
    start: datetime,
    end: datetime,
    cuts: list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    """Return [start,end) minus overlapping cut intervals."""
    if end <= start:
        return []
    pieces: list[tuple[datetime, datetime]] = [(start, end)]
    for c0, c1 in cuts:
        nxt: list[tuple[datetime, datetime]] = []
        for p0, p1 in pieces:
            if c1 <= p0 or c0 >= p1:
                nxt.append((p0, p1))
                continue
            if p0 < c0:
                nxt.append((p0, min(c0, p1)))
            if c1 < p1:
                nxt.append((max(c1, p0), p1))
        pieces = [(a, b) for a, b in nxt if b > a]
    return pieces


def sum_by(segments: list[FocusSegment], *, key) -> list[tuple[str, float]]:
    totals: dict[str, float] = defaultdict(float)
    for s in segments:
        totals[key(s)] += s.duration_s
    return sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))


def top_titles(segments: list[FocusSegment], *, limit: int) -> list[tuple[str, float, str]]:
    totals: dict[tuple[str, str], float] = defaultdict(float)
    for s in segments:
        if not s.title:
            continue
        totals[(s.title, s.app)] += s.duration_s
    ranked = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0][0]))
    return [(title, secs, app) for (title, app), secs in ranked[:limit]]


def hour_apps(
    segments: list[FocusSegment],
    away: list[AwaySegment] | None = None,
    *,
    meetings: list[MeetingSession] | None = None,
) -> list[tuple[str, list[tuple[str, float]]]]:
    """Bucket focus + optional away/meeting sessions into local-hour slices."""
    buckets: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    def _add(start_z: str, duration_s: float, label: str) -> None:
        remaining, cursor = duration_s, parse_ts(start_z).astimezone()
        while remaining > 0.5:
            hour_end = cursor.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            slice_s = min(remaining, (hour_end - cursor).total_seconds())
            if slice_s <= 0:
                break
            buckets[cursor.strftime("%H:00")][label] += slice_s
            remaining -= slice_s
            cursor = hour_end

    for s in segments:
        _add(s.start, s.duration_s, s.app)
    for m in meetings or []:
        _add(m.start, m.duration_s, "meeting" if m.phase == "in_call" else "tab_open")
    for a in away or []:
        if meetings is not None and a.presence == "meeting":
            continue
        _add(a.start, a.duration_s, "meeting" if a.presence == "meeting" else "away")
    return [
        (h, sorted(buckets[h].items(), key=lambda kv: (-kv[1], kv[0])))
        for h in sorted(buckets)
    ]
