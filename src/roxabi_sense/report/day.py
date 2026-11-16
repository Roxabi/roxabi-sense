"""Day recap — compile raw events into a DayRecap.

Text renderers live in ``report.render.day``.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import Any

from roxabi_sense.report.away import IDLE_GAP_S, AwaySegment, _idle_mode, away_segments
from roxabi_sense.report.dwell.aggregate import hour_apps, top_titles
from roxabi_sense.report.dwell.coverage import native_confirmation
from roxabi_sense.report.dwell.current_attention import (
    CurrentAttention,
    focus_key,
    resolve_current_attention,
)
from roxabi_sense.report.dwell.repos import (
    HERDR_KIND,
    AgentRepoTime,
    _with_current_repo,
    attribute_repos,
)
from roxabi_sense.report.dwell.stays import TerminalStayStats, terminal_stay_stats
from roxabi_sense.report.dwell.tern_proofs import project_timeline, switch_metrics
from roxabi_sense.report.dwell.tern_work import TernWork, add_tern_work, summarize_tern_work
from roxabi_sense.report.dwell.truncation import _tern_prefix, clip_inferred, focus_boundary
from roxabi_sense.report.enrich import (
    AgentSessionRow,
    MediaTrack,
    agent_sessions,
    media_tracks,
    processes_seen,
)
from roxabi_sense.report.meetings import evidence
from roxabi_sense.report.meetings.sessions import MeetingSession
from roxabi_sense.report.meetings.signals import annotate_away_with_meetings
from roxabi_sense.report.segments import (
    FocusSegment,
    attention_segments,
    focus_segments,
    horizon_dt,
    switch_count,
)
from roxabi_sense.report.status import current_focus, status_snapshot
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
    # Union of observed joined intervals; inferred calls are reported separately.
    meeting_total_s: float
    meeting_tab_open_s: float
    meeting_sessions: list[MeetingSession]
    meeting_fidelity: str  # observed | partial | inferred | unknown
    meeting_fidelity_note: str
    meeting_inferred_s: float
    meeting_coverage_s: float
    meeting_unknown_s: float
    meeting_quality: str
    focus_in_meeting_s: float
    focus_outside_meeting_s: float
    activity_union_s: float
    events_truncated: bool
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
    # Stretch duration measures credited fragments, not the endpoint envelope.
    current_attention: CurrentAttention | None = None
    tern_work: TernWork | None = None

    def to_dict(self) -> dict[str, Any]:
        body = asdict(self)
        for name in ("focus_segments", "attention_segments"):
            for segment in body[name]:
                segment.pop("context_ref", None)
                segment.pop("repo_ref", None)
        return body

def compile_day_recap(
    store: Store,
    day: str | None = None,
    *,
    now: datetime | None = None,
) -> DayRecap:
    """Build a DayRecap from store events for a local calendar day."""
    start, end = store.day_bounds(day)
    day_label = day or datetime.now().astimezone().date().isoformat()
    raw_events = store.events_for_day(day, kinds=(), limit=_DAY_EVENT_LIMIT)
    horizon = horizon_dt(end, now)
    events = [event for event in raw_events if parse_ts(event.ts) <= horizon]
    kind_counts: Counter[str] = Counter(e.kind for e in events)
    first_ts = events[0].ts if events else None
    last_ts = events[-1].ts if events else None
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
    day_start = parse_ts(start)
    meeting_end = max(day_start, horizon)
    sessions, inferred_sessions, observed, meeting_coverage, truncated = (
        evidence.compile_day_meetings(store, events, day_start, meeting_end)
    )
    truncated = truncated or len(raw_events) >= _DAY_EVENT_LIMIT
    focus_end = focus_boundary(events, day_start, horizon) if truncated else horizon
    if truncated:
        meeting_coverage = []
        inferred_sessions = clip_inferred(
            inferred_sessions, parse_ts(last_ts) if last_ts else day_start
        )
        sessions = sorted(
            evidence.observed_sessions(observed) + inferred_sessions, key=lambda m: m.start
        )
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
    native_focus = ([prior_focus] if prior_focus is not None else []) + focus_ev
    raw_focus = list(focus_ev)
    if prior_focus is not None:
        carried = replace(prior_focus, ts=start)
        raw_focus.insert(0, carried)
        if (
            prior_coverage is not None
            and prior_coverage.payload.get("state") == "observed"
            and prior_coverage.payload.get("source") == "cosmic"
            and bool(prior_coverage.payload.get("window_id"))
            and prior_focus.payload.get("source") == "cosmic"
            and prior_focus.payload.get("window_id") == prior_coverage.payload.get("window_id")
        ):
            focus_ev.insert(0, carried)
    overall_coverage = store.last_by_kind("focus_coverage") if not truncated else None
    raw_observation = store.get_meta("focus_observation") if not truncated else None
    snapshots, tern_meta, prefix_end = _tern_prefix(store, events, start, horizon, truncated)
    projection = project_timeline(
        raw_focus, snapshots, tern_meta, focus_end, history_ids={event.id for event in focus_ev},
        source_times={prior_focus.id: prior_focus.ts} if prior_focus else None)
    native = native_confirmation(
        coverage, native_focus, raw_observation,
        overall_coverage.id if overall_coverage is not None else None, focus_end,
        keys={record.raw.id: focus_key(record.occurrence) for record in projection.records},
    )
    segments = focus_segments(projection, away, horizon=focus_end, native=native)
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
    repos = add_tern_work(repos, snapshots, tern_meta, segments, parse_ts(start), prefix_end,
                          coverage=native)
    modes = {a.mode for a in away}
    idle_mode = _idle_mode(modes, bool(away))
    pure_away = [a for a in away if a.presence != "meeting"]
    inferred_calls = [
        (parse_ts(m.start), parse_ts(m.end)) for m in inferred_sessions if m.phase == "in_call"
    ]
    meeting_total_s = evidence.seconds(observed)
    meeting_inferred_s = evidence.seconds(evidence.subtract(inferred_calls, observed))
    meeting_tab_open_s = evidence.seconds([
        (parse_ts(m.start), parse_ts(m.end)) for m in inferred_sessions if m.phase == "tab_open"
    ])
    meeting_coverage_s = evidence.seconds(meeting_coverage)
    meeting_unknown_s = max(0.0, (meeting_end - day_start).total_seconds() - meeting_coverage_s)
    meeting_quality = ("observed" if meeting_coverage_s > 0 and meeting_unknown_s == 0 else
                       "partial" if meeting_coverage_s > 0 or meeting_total_s > 0 else
                       "inferred" if meeting_inferred_s > 0 else "unknown")
    fidelity = meeting_quality
    fidelity_note = "Chrome Google Meet scope; observed UI only; uncovered time is unknown."
    focus_intervals = [(parse_ts(s.start), parse_ts(s.end)) for s in segments]
    focus_in_meeting_s = evidence.seconds(evidence.intersect(focus_intervals, observed))
    focus_outside_meeting_s = evidence.seconds(evidence.subtract(focus_intervals, observed))
    activity_union_s = evidence.seconds(evidence.union(focus_intervals + observed))
    title_sw, ctx_sw = switch_metrics(projection, segments, attn)
    app_sw = switch_count(segments, key=lambda s: s.app)
    active_focus, active_quality = (
        current_focus(status_snapshot(store), now=horizon, proof_index=projection.index)
        if not truncated else (None, "unknown")
    )
    repos = _with_current_repo(repos, active_focus)
    current_attention = resolve_current_attention(
        current=active_focus, quality=active_quality, focus=projection, native=native,
        attention=attn, away=away, day_start=parse_ts(start), now=horizon,
    )
    return DayRecap(
        day=day_label, start=start, end=end,
        event_count=len(events),
        kind_counts=dict(sorted(kind_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        first_event=first_ts, last_event=last_ts,
        focus_switches=title_sw, focus_switches_app=app_sw, focus_switches_context=ctx_sw,
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
        meeting_inferred_s=meeting_inferred_s, meeting_coverage_s=meeting_coverage_s,
        meeting_unknown_s=meeting_unknown_s, meeting_quality=meeting_quality,
        focus_in_meeting_s=focus_in_meeting_s,
        focus_outside_meeting_s=focus_outside_meeting_s,
        activity_union_s=activity_union_s,
        events_truncated=truncated,
        idle_mode=idle_mode,
        time_by_app=time_by_app,
        top_apps=apps,
        time_by_repo=repos.focus,
        agent_time_by_repo=repos.agents,
        focused_repo_now=None if truncated else repos.focused_repo_now,
        top_titles=top_titles(segments, limit=12),
        agent_sessions=agent_sessions(events),
        processes_seen=processes_seen(events),
        media=media_tracks(events),
        idle_events=kind_counts.get("idle", 0),
        hour_apps=hour_apps(segments, away, meetings=sessions),
        session_shape=session_shape(attn, away),
        focus_coverage_quality=(
            "unknown" if truncated else "bounded"
            if raw_observation and coverage
            and all(e.payload.get("state") != "legacy_unverified" for e in coverage)
            and all(e.ts >= coverage[0].ts for e in focus_ev)
            else "legacy_unverified"
        ),
        current_attention=current_attention,
        tern_work=summarize_tern_work(snapshots, tern_meta, segments, parse_ts(start), prefix_end,
                                      coverage=native),
    )


