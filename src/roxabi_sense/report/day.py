"""Day recap — compile raw events into a DayRecap.

Text renderers live in ``report.render.day``.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from itertools import pairwise
from typing import Any

from roxabi_sense.report.away import IDLE_GAP_S, AwaySegment, away_segments
from roxabi_sense.report.dwell.aggregate import hour_apps, top_titles
from roxabi_sense.report.dwell.repos import HERDR_KIND, AgentRepoTime, attribute_repos
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
from roxabi_sense.report.status import current_focus, status_snapshot
from roxabi_sense.report.top_apps import AppDwell, session_shape, top_apps
from roxabi_sense.store import Event, Store
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
    # Human focus per repo: terminal focus × Herdr focused pane (#79).
    time_by_repo: list[tuple[str, float]]
    # Agent progress per repo: wall time with a Herdr pane `working` (focused or not).
    agent_time_by_repo: list[AgentRepoTime]
    focused_repo_now: str | None
    top_titles: list[tuple[str, float, str]]
    agent_sessions: list[AgentSessionRow]
    processes_seen: list[str]
    media: list[MediaTrack]
    idle_events: int
    hour_apps: list[tuple[str, list[tuple[str, float]]]]
    session_shape: str | None
    focus_coverage_quality: str = "legacy_unverified"
    current_focus_quality: str = "legacy_unverified"
    current_focus_binding: tuple[str, str, str | None] | None = None

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
    prior_coverage = store.last_by_kind_before("focus_coverage", start)
    prior_legacy = store.last_by_kind_before("focus_legacy", start)
    if prior_legacy is not None and (
        prior_coverage is None or (prior_legacy.ts, prior_legacy.id) > (
            prior_coverage.ts, prior_coverage.id
        )
    ):
        prior_coverage = replace(
            prior_legacy, kind="focus_coverage",
            payload={**prior_legacy.payload, "state": "legacy_unverified"},
        )
    sessions = meeting_sessions(events, horizon=horizon)
    away = annotate_away_with_meetings(
        away_segments(
            [prior_coverage, *events] if prior_coverage is not None else events,
            horizon=horizon,
            gap_s=IDLE_GAP_S,
            window_start=parse_ts(start),
            prior_idle=prior_idle,
        ),
        sessions,
    )
    focus_ev = [e for e in events if e.kind == "focus"]
    coverage = [
        e if e.kind == "focus_coverage" else replace(
            e, kind="focus_coverage", payload={**e.payload, "state": "legacy_unverified"}
        )
        for e in events if e.kind in {"focus_coverage", "focus_legacy"}
    ]
    if prior_coverage is not None:
        coverage.insert(0, prior_coverage)
        prior_focus = store.last_by_kind_before("focus", start)
        if prior_focus is not None:
            focus_ev.insert(0, replace(prior_focus, ts=start))
    latest_coverage = coverage[-1] if coverage else None
    overall_coverage = store.last_by_kind("focus_coverage")
    raw_observation = store.get_meta("focus_observation")
    if (
        latest_coverage is not None
        and overall_coverage is not None
        and latest_coverage.id == overall_coverage.id
        and latest_coverage.payload.get("state") == "observed"
    ):
        try:
            observation = json.loads(raw_observation) if raw_observation else {}
            observed_end = (
                observation.get("last_observed_at") if isinstance(observation, dict) else None
            )
            if (
                not isinstance(observation, dict)
                or observation.get("coverage_id") != latest_coverage.id
                or observation.get("source") != latest_coverage.payload.get("source")
                or observation.get("window_id") != latest_coverage.payload.get("window_id")
                or not isinstance(observed_end, str)
            ):
                observed_end = latest_coverage.ts
            parse_ts(observed_end)
        except (TypeError, ValueError):
            observed_end = latest_coverage.ts
        coverage.append(Event(
            id=latest_coverage.id + 1, ts=max(latest_coverage.ts, observed_end),
            kind="focus_coverage",
            payload={"state": "unknown", "source": latest_coverage.payload.get("source")},
        ))
    segments = focus_segments(focus_ev, away, horizon=horizon, coverage_events=coverage)
    attn = attention_segments(segments)
    stays = terminal_stay_stats(attn)

    apps = top_apps(segments, limit=20)
    time_by_app = [(a.app, a.seconds) for a in apps]
    repos = attribute_repos(
        events,
        segments,
        window_start=parse_ts(start),
        horizon=horizon,
        prior=store.last_by_kind_before(HERDR_KIND, start),
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
    title_sw = sum(a.end == b.start for a, b in pairwise(segments))
    app_sw = switch_count(segments, key=lambda s: s.app)
    ctx_sw = switch_count(attn)
    active_focus, active_quality = current_focus(status_snapshot(store), now=horizon)
    binding = None
    if active_focus is not None:
        window_id = active_focus.payload.get("window_id")
        binding = (
            active_focus.ts,
            str(active_focus.payload.get("app") or ""),
            str(window_id) if window_id else None,
        )

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
        time_by_repo=repos.focus,
        agent_time_by_repo=repos.agents,
        focused_repo_now=repos.focused_repo_now,
        top_titles=top_titles(segments, limit=12),
        agent_sessions=agent_sessions(events),
        processes_seen=processes_seen(events),
        media=media_tracks(events),
        idle_events=kind_counts.get("idle", 0),
        hour_apps=hour_apps(segments, away, meetings=sessions),
        session_shape=session_shape(attn, away),
        focus_coverage_quality=(
            "bounded"
            if raw_observation and coverage
            and all(e.payload.get("state") != "legacy_unverified" for e in coverage)
            and all(e.ts >= coverage[0].ts for e in focus_ev)
            else "legacy_unverified"
        ),
        current_focus_quality=active_quality,
        current_focus_binding=binding,
    )


def _idle_mode(modes: set[str], has_away: bool) -> str:
    if any(m.startswith("wayland") for m in modes):
        return "wayland-idle"
    if "logind" in modes:
        return "logind"
    if "degraded-gap" in modes:
        return "degraded-gap"
    return "mixed" if has_away else "none"
