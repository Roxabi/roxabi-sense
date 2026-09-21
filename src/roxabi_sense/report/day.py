"""Day recap — compile raw events into a DayRecap.

Text renderers live in ``report.render.day``.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from roxabi_sense.report.away import IDLE_GAP_S, AwaySegment, away_segments
from roxabi_sense.report.dwell.aggregate import hour_apps, sum_by, top_titles
from roxabi_sense.report.dwell.stays import TerminalStayStats, terminal_stay_stats
from roxabi_sense.report.enrich import (
    AgentSessionRow,
    MediaTrack,
    agent_sessions,
    media_tracks,
    processes_seen,
)
from roxabi_sense.report.meeting import annotate_away_with_meetings
from roxabi_sense.report.meeting_fidelity import meeting_fidelity_from_events
from roxabi_sense.report.meeting_sessions import MeetingSession, meeting_sessions
from roxabi_sense.report.segments import (
    FocusSegment,
    attention_segments,
    focus_segments,
    horizon_dt,
    switch_count,
)
from roxabi_sense.report.top_apps import AppDwell, session_shape, top_apps
from roxabi_sense.store import Store
from roxabi_sense.util.time import parse_ts

_DAY_EVENT_LIMIT = 50_000
@dataclass
class DayRecap:
    day: str
    start: str
    end: str
    event_count: int
    kind_counts: dict[str, int]
    first_event: str | None
    last_event: str | None
    # Title-grain (fine) segment boundaries — noisy under AT-SPI agent renames.
    focus_switches: int
    # App-only switches (ghostty→slack); undercounts multi-ghostty contexts.
    focus_switches_app: int
    # Attention-key hops (session/agent); short 3–5s agent checks count.
    focus_switches_context: int
    focus_segments: list[FocusSegment]
    attention_segments: list[FocusSegment]
    # Complementary: continuous terminal visits (how long you stay, not hop count).
    terminal_stays: TerminalStayStats
    away_segments: list[AwaySegment]
    away_total_s: float
    # ADR-004: meeting_total_s = Σ in_call sessions only (not idle overlay sum).
    meeting_total_s: float
    meeting_tab_open_s: float
    meeting_sessions: list[MeetingSession]
    meeting_fidelity: str  # full | active_only | none | unknown (ADR-004)
    meeting_fidelity_note: str
    idle_mode: str
    time_by_app: list[tuple[str, float]]
    top_apps: list[AppDwell]
    time_by_repo: list[tuple[str, float]]
    top_titles: list[tuple[str, float, str]]
    agent_sessions: list[AgentSessionRow]
    processes_seen: list[str]
    media: list[MediaTrack]
    idle_events: int
    hour_apps: list[tuple[str, list[tuple[str, float]]]]
    session_shape: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

def compile_day_recap(
    store: Store,
    day: str | None = None,
    *,
    now: datetime | None = None,
) -> DayRecap:
    """Build a DayRecap from store events for a local calendar day."""
    start, end = store.day_bounds(day)
    day_label = day or datetime.now().astimezone().date().isoformat()
    events = store.events_for_day(day, kinds=(), limit=_DAY_EVENT_LIMIT)

    kind_counts: Counter[str] = Counter(e.kind for e in events)
    first_ts = events[0].ts if events else None
    last_ts = events[-1].ts if events else None

    horizon = horizon_dt(end, now)
    # Carry-in prior-day idle so overnight leave-open does not soak last app (ADR-002).
    prior_idle = store.last_by_kind_before("idle", start)
    sessions = meeting_sessions(events, horizon=horizon)
    away = annotate_away_with_meetings(
        away_segments(
            events,
            horizon=horizon,
            gap_s=IDLE_GAP_S,
            window_start=parse_ts(start),
            prior_idle=prior_idle,
        ),
        sessions,
    )
    focus_ev = [e for e in events if e.kind == "focus"]
    segments = focus_segments(focus_ev, away, horizon=horizon)
    attn = attention_segments(segments)
    stays = terminal_stay_stats(attn)

    apps = top_apps(segments, limit=20)
    time_by_app = [(a.app, a.seconds) for a in apps]
    time_by_repo = sum_by(
        [s for s in segments if s.cwd],
        key=lambda s: _repo_label(s.cwd or ""),
    )
    modes = {a.mode for a in away}
    idle_mode = _idle_mode(modes, bool(away))
    pure_away = [a for a in away if a.presence != "meeting"]
    in_call = [m for m in sessions if m.phase == "in_call"]
    tab_open = [m for m in sessions if m.phase == "tab_open"]
    meeting_total_s = sum(m.duration_s for m in in_call)
    meeting_tab_open_s = sum(m.duration_s for m in tab_open)
    fidelity, fidelity_note = meeting_fidelity_from_events(
        events,
        focus_backend=store.get_meta("focus_backend"),
    )
    title_sw = max(0, len(segments) - 1) if segments else 0
    app_sw = switch_count(segments, key=lambda s: s.app)
    ctx_sw = max(0, len(attn) - 1) if attn else 0

    return DayRecap(
        day=day_label,
        start=start,
        end=end,
        event_count=len(events),
        kind_counts=dict(sorted(kind_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        first_event=first_ts,
        last_event=last_ts,
        focus_switches=title_sw,
        focus_switches_app=app_sw,
        focus_switches_context=ctx_sw,
        focus_segments=segments,
        attention_segments=attn,
        terminal_stays=stays,
        away_segments=away,
        away_total_s=sum(a.duration_s for a in pure_away),
        meeting_total_s=meeting_total_s,
        meeting_tab_open_s=meeting_tab_open_s,
        meeting_sessions=sessions,
        meeting_fidelity=fidelity,
        meeting_fidelity_note=fidelity_note,
        idle_mode=idle_mode,
        time_by_app=time_by_app,
        top_apps=apps,
        time_by_repo=time_by_repo,
        top_titles=top_titles(segments, limit=12),
        agent_sessions=agent_sessions(events),
        processes_seen=processes_seen(events),
        media=media_tracks(events),
        idle_events=kind_counts.get("idle", 0),
        hour_apps=hour_apps(segments, away, meetings=sessions),
        session_shape=session_shape(attn, away),
    )


def _idle_mode(modes: set[str], has_away: bool) -> str:
    if any(m.startswith("wayland") for m in modes):
        return "wayland-idle"
    if "logind" in modes:
        return "logind"
    if "degraded-gap" in modes:
        return "degraded-gap"
    return "mixed" if has_away else "none"


def _repo_label(cwd: str) -> str:
    p = Path(cwd.rstrip("/"))
    if not p.name:
        return cwd
    parts = p.parts
    if "projects" in parts:
        tail = parts[parts.index("projects") + 1 :]
        if len(tail) >= 2:
            return "/".join(tail[:2])
        if tail:
            return tail[0]
    return p.name
