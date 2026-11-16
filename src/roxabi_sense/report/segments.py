"""Focus dwell segments; away gaps live in ``report.away`` (ADR-002).

Two grains:
- **fine** (``focus_segments``): app + title — top windows / timeline
- **attention** (``attention_segments``): context key (session/agent) —
  multitasking hops including short 3–5s agent checks; title thrash collapsed

Terminal stay stats live in ``report.dwell.stays``. Rollups live in
``report.dwell.aggregate``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from roxabi_sense.report.away import AwaySegment
from roxabi_sense.report.dwell.coverage import coverage_cuts, identity_cuts
from roxabi_sense.report.dwell.projection import project_focus
from roxabi_sense.store import Event
from roxabi_sense.util.time import parse_ts, to_z

MIN_DWELL_S = 3.0  # ignore micro-focus flickers (fine title segments)
_APP_ALIASES: dict[str, str] = {
    "unnamed": "ghostty",
    "com.mitchellh.ghostty": "ghostty",
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
    window_id: str | None = None
    context_ref: str | None = None
    repo_ref: str | None = None


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
    if seg.context_ref:
        return (app, "context", seg.context_ref)
    if seg.window_id:
        return (app, "window", seg.window_id)
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
    previous = segments[0]
    for current in segments[1:]:
        if previous.end == current.start and key_fn(current) != key_fn(previous):
            n += 1
        previous = current
    return n


def is_terminal_app(app: str) -> bool:
    return norm_app(app).lower() in _TERMINAL_APPS


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
        if k == run_key and seg.start == end:
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


def _prefer_meta(base: FocusSegment, other: FocusSegment) -> FocusSegment:
    """Never revive cleared Tern metadata; preserve legacy metadata behavior."""
    if base.context_ref or other.context_ref:
        return replace(other, start=base.start, end=base.end, duration_s=base.duration_s)
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
    coverage_events: list[Event] | None = None,
) -> list[FocusSegment]:
    """Attribute focus dwell (fine grain: app+title), cutting out away gaps."""
    if not focus_events:
        return []
    collapsed: list[tuple[datetime, Any]] = []
    for e in focus_events:
        fields = project_focus(e)
        t = parse_ts(e.ts)
        if collapsed and collapsed[-1][1] == fields:
            continue
        collapsed.append((t, fields))

    away_ranges = [(parse_ts(a.start), parse_ts(a.end)) for a in away]
    coverage = sorted(coverage_events or [], key=lambda e: (e.ts, e.id))
    away_ranges.extend(coverage_cuts(coverage, focus_events, horizon))
    segs: list[FocusSegment] = []
    for i, row in enumerate(collapsed):
        t0, fields = row
        (app, title, cwd, agent, win_pid, sid, match_s, agent_pid, window_id,
         source, context_ref, repo_ref) = fields
        t1 = collapsed[i + 1][0] if i + 1 < len(collapsed) else horizon
        if t1 < t0:
            t1 = t0
        mismatches = identity_cuts(coverage, horizon, source, window_id)
        for a0, a1 in _subtract_ranges(t0, t1, away_ranges + mismatches):
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
                    window_id=window_id,
                    context_ref=context_ref,
                    repo_ref=repo_ref,
                )
            )
    return segs




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
