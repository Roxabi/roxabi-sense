"""Day recap — compile raw events into a human-readable attention summary."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from roxabi_sense.report.enrich import (
    AgentSessionRow,
    MediaTrack,
    agent_sessions,
    media_tracks,
    processes_seen,
)
from roxabi_sense.report.meeting import annotate_away_with_meetings
from roxabi_sense.report.meeting_sessions import (
    MeetingSession,
    format_meeting_sessions,
    meeting_sessions,
)
from roxabi_sense.report.segments import (
    IDLE_GAP_S,
    MIN_DWELL_S,
    AwaySegment,
    FocusSegment,
    away_segments,
    focus_segments,
    horizon_dt,
    hour_apps,
    parse_ts,
    sum_by,
    top_titles,
)
from roxabi_sense.report.top_apps import AppDwell, session_shape, top_apps
from roxabi_sense.store import Store

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
    focus_switches: int
    focus_segments: list[FocusSegment]
    away_segments: list[AwaySegment]
    away_total_s: float
    # ADR-004: meeting_total_s = Σ in_call sessions only (not idle overlay sum).
    meeting_total_s: float
    meeting_tab_open_s: float
    meeting_sessions: list[MeetingSession]
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
    gap_s = IDLE_GAP_S
    # Sessions first (shared evidence); then idle overlay from in_call spans (ADR-004).
    sessions = meeting_sessions(events, horizon=horizon)
    away = annotate_away_with_meetings(
        away_segments(events, horizon=horizon, gap_s=gap_s),
        sessions,
    )
    focus_ev = [e for e in events if e.kind == "focus"]
    segments = focus_segments(focus_ev, away, horizon=horizon)

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

    return DayRecap(
        day=day_label,
        start=start,
        end=end,
        event_count=len(events),
        kind_counts=dict(sorted(kind_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        first_event=first_ts,
        last_event=last_ts,
        focus_switches=max(0, len(segments) - 1) if segments else 0,
        focus_segments=segments,
        away_segments=away,
        away_total_s=sum(a.duration_s for a in pure_away),
        meeting_total_s=meeting_total_s,
        meeting_tab_open_s=meeting_tab_open_s,
        meeting_sessions=sessions,
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
        session_shape=session_shape(segments, away),
    )

def format_day_recap(recap: DayRecap, *, max_titles: int = 10, max_hours: int = 24) -> str:
    """Human-readable multi-line recap."""
    lines: list[str] = []
    span = _fmt_span(recap.first_event, recap.last_event)
    tracked = sum(s for _, s in recap.time_by_app)
    totals = (
        f"focus_dwell={_fmt_dur(tracked)}   away={_fmt_dur(recap.away_total_s)}"
    )
    if recap.meeting_total_s > 0:
        totals += f"   meeting={_fmt_dur(recap.meeting_total_s)}"
    if recap.meeting_tab_open_s > 0:
        totals += f"   tab_open={_fmt_dur(recap.meeting_tab_open_s)}"
    lines.append(f"sense recap  {recap.day}")
    lines.append(f"window: {span}   events={recap.event_count}   {totals}")
    if recap.session_shape:
        lines.append(f"session_shape: {recap.session_shape}")
    if recap.kind_counts:
        kinds = "  ".join(f"{k}={v}" for k, v in list(recap.kind_counts.items())[:8])
        lines.append(f"kinds: {kinds}")

    lines.extend(
        format_meeting_sessions(
            recap.meeting_sessions,
            in_call_s=recap.meeting_total_s,
            tab_open_s=recap.meeting_tab_open_s,
            fmt_dur=_fmt_dur,
            local_hm=_local_hm,
        )
    )

    lines.append("")
    n_away = sum(1 for a in recap.away_segments if a.presence != "meeting")
    n_meet = sum(1 for a in recap.away_segments if a.presence == "meeting")
    lines.append(
        f"Idle gaps (mode={recap.idle_mode}, gap≥{_fmt_dur(IDLE_GAP_S)}, "
        f"away={n_away} meeting={n_meet})"
    )
    if recap.away_segments:
        for a in recap.away_segments[:20]:
            tag = a.presence if a.presence == "meeting" else "away"
            extra = ""
            if a.presence == "meeting" and a.meeting_label:
                label = a.meeting_label
                if len(label) > 48:
                    label = label[:47] + "…"
                extra = f" · {label}"
            lines.append(
                f"  {_local_hm(a.start)}–{_local_hm(a.end)}  {_fmt_dur(a.duration_s):>8}  "
                f"[{a.mode}] {tag}{extra}"
            )
        if len(recap.away_segments) > 20:
            lines.append(f"  … +{len(recap.away_segments) - 20} more")
    else:
        lines.append("  (none)")

    lines.append("")
    lines.append(f"Top apps (dwell ≥{MIN_DWELL_S:.0f}s, away cut out)")
    if recap.top_apps:
        for row in recap.top_apps[:12]:
            pct = row.share * 100
            lines.append(
                f"  {_pad(row.app, 22)} {_fmt_dur(row.seconds):>8}"
                f"  ({row.minutes:g}m)  {pct:5.1f}%"
            )
    elif recap.time_by_app:
        for app, secs in recap.time_by_app[:12]:
            pct = (secs / tracked * 100) if tracked else 0
            lines.append(f"  {_pad(app, 22)} {_fmt_dur(secs):>8}  {pct:5.1f}%")
    else:
        lines.append("  (no focus events)")

    if recap.time_by_repo:
        lines.append("")
        lines.append("Repos (from focus agent.cwd)")
        repo_total = sum(s for _, s in recap.time_by_repo)
        for repo, secs in recap.time_by_repo[:12]:
            pct = (secs / repo_total * 100) if repo_total else 0
            lines.append(f"  {_pad(repo, 28)} {_fmt_dur(secs):>8}  {pct:5.1f}%")

    if recap.top_titles:
        lines.append("")
        lines.append("Top windows")
        for title, secs, app in recap.top_titles[:max_titles]:
            t = title if len(title) <= 56 else title[:55] + "…"
            lines.append(f"  {_fmt_dur(secs):>8}  [{app}] {t}")

    if recap.agent_sessions:
        lines.append("")
        lines.append(f"Agent sessions seen open ({len(recap.agent_sessions)})")
        for s in recap.agent_sessions:
            cwd = s.cwd or "—"
            sid = (s.session_id or "")[:8]
            lines.append(
                f"  {s.agent:7}  {sid:8}  {_local_hm(s.first_seen)}–{_local_hm(s.last_seen)}  {cwd}"
            )

    if recap.processes_seen:
        lines.append("")
        lines.append("Apps present: " + ", ".join(recap.processes_seen))

    if recap.media:
        lines.append("")
        lines.append("Media")
        for m in recap.media[:8]:
            art = m.artist or "?"
            tit = m.title or "?"
            lines.append(f"  {_local_hm(m.first_seen)}  {m.player}: {art} — {tit}")

    if recap.hour_apps:
        lines.append("")
        lines.append("By hour (local, includes away/meeting)")
        for hour, apps in recap.hour_apps[:max_hours]:
            bits = " · ".join(f"{a} {_fmt_dur(s)}" for a, s in apps[:4])
            lines.append(f"  {hour}  {bits}")

    return "\n".join(lines)

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

def _fmt_dur(seconds: float) -> str:
    s = int(round(seconds))
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m{s:02d}s" if s else f"{m}m"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"

def _fmt_span(first: str | None, last: str | None) -> str:
    if not first and not last:
        return "—"
    return f"{_local_hm(first) if first else '?'} → {_local_hm(last) if last else '?'} local"

def _local_hm(ts: str) -> str:
    return parse_ts(ts).astimezone().strftime("%H:%M")

def _pad(text: str, width: int) -> str:
    return text.ljust(width) if len(text) <= width else text[: width - 1] + "…"
