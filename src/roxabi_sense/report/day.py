"""Day recap — compile raw events into a human-readable attention summary."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from roxabi_sense.report.away import IDLE_GAP_S, AwaySegment, away_segments
from roxabi_sense.report.enrich import (
    AgentSessionRow,
    MediaTrack,
    agent_sessions,
    media_tracks,
    processes_seen,
)
from roxabi_sense.report.meeting import annotate_away_with_meetings
from roxabi_sense.report.meeting_fidelity import meeting_fidelity_from_events
from roxabi_sense.report.meeting_sessions import (
    MeetingSession,
    format_meeting_sessions,
    meeting_sessions,
)
from roxabi_sense.report.segments import (
    MIN_DWELL_S,
    FocusSegment,
    TerminalStayStats,
    attention_segments,
    focus_segments,
    horizon_dt,
    hour_apps,
    sum_by,
    switch_count,
    terminal_stay_stats,
    top_titles,
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

def format_day_recap_share(
    recap: DayRecap,
    *,
    max_apps: int = 5,
    max_repos: int = 4,
    max_titles: int = 4,
) -> str:
    """Shareable day card as Markdown tables (Slack/GitHub/notes).

    No idle lists, no full agent dump — scannable tables only.
    """
    tracked = sum(s for _, s in recap.time_by_app)
    window = "—"
    if recap.first_event and recap.last_event:
        window = f"{_local_hm(recap.first_event)}–{_local_hm(recap.last_event)}"
    meet = (
        _fmt_dur(recap.meeting_total_s)
        if recap.meeting_total_s > 0
        else "—"
    )
    shape = recap.session_shape or "—"

    blocks: list[str] = [f"**sense · {recap.day}**", ""]
    blocks.extend(
        _md_table(
            ["metric", "value"],
            [
                ["focus", _fmt_dur(tracked)],
                ["away", _fmt_dur(recap.away_total_s)],
                ["meet", meet],
                ["shape", shape],
                ["window", window],
                ["agents", str(len(recap.agent_sessions))],
            ],
        )
    )
    blocks.append("")
    blocks.extend(
        _md_table(
            ["switches", "n"],
            [
                ["app", str(recap.focus_switches_app)],
                ["ctx", str(recap.focus_switches_context)],
                ["title", str(recap.focus_switches)],
            ],
        )
    )

    ts = recap.terminal_stays
    if ts.visits:
        blocks.append("")
        blocks.extend(
            _md_table(
                ["terminal", "value"],
                [
                    ["visits", str(ts.visits)],
                    ["median", _fmt_dur(ts.median_s)],
                    ["≥2m", f"{ts.ge_2m} · {_fmt_dur(ts.time_ge_2m_s)}"],
                    ["≥5m", f"{ts.ge_5m} · {_fmt_dur(ts.time_ge_5m_s)}"],
                    ["≥10m", f"{ts.ge_10m} · {_fmt_dur(ts.time_ge_10m_s)}"],
                ],
            )
        )

    app_rows: list[list[str]] = []
    if recap.top_apps:
        for a in recap.top_apps[:max_apps]:
            app_rows.append(
                [
                    _short_app(a.app),
                    _fmt_dur(a.seconds),
                    f"{a.share * 100:.0f}%",
                ]
            )
    elif recap.time_by_app:
        total = sum(s for _, s in recap.time_by_app) or 1.0
        for name, secs in recap.time_by_app[:max_apps]:
            app_rows.append(
                [
                    _short_app(name),
                    _fmt_dur(secs),
                    f"{secs / total * 100:.0f}%",
                ]
            )
    if app_rows:
        blocks.append("")
        blocks.extend(_md_table(["app", "time", "%"], app_rows))

    if recap.time_by_repo:
        repo_total = sum(s for _, s in recap.time_by_repo) or 1.0
        repo_rows = [
            [
                _short_repo(r),
                _fmt_dur(s),
                f"{s / repo_total * 100:.0f}%",
            ]
            for r, s in recap.time_by_repo[:max_repos]
        ]
        blocks.append("")
        blocks.extend(_md_table(["repo", "time", "%"], repo_rows))

    if recap.top_titles:
        title_rows = [
            [_short_title(t, max_len=36), _fmt_dur(s)]
            for t, s, _app in recap.top_titles[:max_titles]
        ]
        blocks.append("")
        blocks.extend(_md_table(["top window", "time"], title_rows))

    return "\n".join(blocks)


def format_day_recap(recap: DayRecap, *, max_titles: int = 10, max_hours: int = 24) -> str:
    """Human-readable multi-line recap."""
    lines: list[str] = []
    tracked = sum(s for _, s in recap.time_by_app)
    totals = f"focus_dwell={_fmt_dur(tracked)}   away={_fmt_dur(recap.away_total_s)}"
    if recap.meeting_total_s > 0:
        totals += f"   meeting={_fmt_dur(recap.meeting_total_s)}"
    if recap.meeting_tab_open_s > 0:
        totals += f"   tab_open={_fmt_dur(recap.meeting_tab_open_s)}"
    lines += [
        f"sense recap  {recap.day}",
        f"window: {_fmt_span(recap.first_event, recap.last_event)}   "
        f"events={recap.event_count}   {totals}",
    ]
    if recap.session_shape:
        lines.append(f"session_shape: {recap.session_shape}")
    lines.append(
        f"focus_switches: {recap.focus_switches_app} app · "
        f"{recap.focus_switches_context} ctx · {recap.focus_switches} title"
    )
    ts = recap.terminal_stays
    if ts.visits:
        lines.append(
            f"terminal_stays: {ts.visits} visits · median {_fmt_dur(ts.median_s)} · "
            f"≥2m {ts.ge_2m} ({_fmt_dur(ts.time_ge_2m_s)}) · "
            f"≥5m {ts.ge_5m} ({_fmt_dur(ts.time_ge_5m_s)}) · "
            f"≥10m {ts.ge_10m} ({_fmt_dur(ts.time_ge_10m_s)})"
        )
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
            fidelity=recap.meeting_fidelity,
            fidelity_note=recap.meeting_fidelity_note,
        )
    )
    n_away = sum(1 for a in recap.away_segments if a.presence != "meeting")
    n_meet = sum(1 for a in recap.away_segments if a.presence == "meeting")
    lines += [
        "",
        f"Idle gaps (mode={recap.idle_mode}, gap≥{_fmt_dur(IDLE_GAP_S)}, "
        f"away={n_away} meeting={n_meet})",
    ]
    if recap.away_segments:
        for a in recap.away_segments[:20]:
            tag = a.presence if a.presence == "meeting" else "away"
            extra = ""
            if a.presence == "meeting" and a.meeting_label:
                lab = a.meeting_label
                extra = f" · {lab if len(lab) <= 48 else lab[:47] + '…'}"
            lines.append(
                f"  {_local_hm(a.start)}–{_local_hm(a.end)}  {_fmt_dur(a.duration_s):>8}  "
                f"[{a.mode}] {tag}{extra}"
            )
        if len(recap.away_segments) > 20:
            lines.append(f"  … +{len(recap.away_segments) - 20} more")
    else:
        lines.append("  (none)")
    lines += ["", f"Top apps (dwell ≥{MIN_DWELL_S:.0f}s, away cut out)"]
    if recap.top_apps:
        for row in recap.top_apps[:12]:
            lines.append(
                f"  {_pad(row.app, 22)} {_fmt_dur(row.seconds):>8}"
                f"  ({row.minutes:g}m)  {row.share * 100:5.1f}%"
            )
    elif recap.time_by_app:
        for app, secs in recap.time_by_app[:12]:
            pct = (secs / tracked * 100) if tracked else 0
            lines.append(f"  {_pad(app, 22)} {_fmt_dur(secs):>8}  {pct:5.1f}%")
    else:
        lines.append("  (no focus events)")
    if recap.time_by_repo:
        lines += ["", "Repos (from focus agent.cwd)"]
        repo_total = sum(s for _, s in recap.time_by_repo)
        for repo, secs in recap.time_by_repo[:12]:
            pct = (secs / repo_total * 100) if repo_total else 0
            lines.append(f"  {_pad(repo, 28)} {_fmt_dur(secs):>8}  {pct:5.1f}%")
    if recap.top_titles:
        lines += ["", "Top windows"]
        for title, secs, app in recap.top_titles[:max_titles]:
            t = title if len(title) <= 56 else title[:55] + "…"
            lines.append(f"  {_fmt_dur(secs):>8}  [{app}] {t}")
    if recap.agent_sessions:
        lines += ["", f"Agent sessions seen open ({len(recap.agent_sessions)})"]
        for s in recap.agent_sessions:
            sid = (s.session_id or "")[:8]
            lines.append(
                f"  {s.agent:7}  {sid:8}  {_local_hm(s.first_seen)}–{_local_hm(s.last_seen)}  "
                f"{s.cwd or '—'}"
            )
    if recap.processes_seen:
        lines += ["", "Apps present: " + ", ".join(recap.processes_seen)]
    if recap.media:
        lines += ["", "Media"]
        for m in recap.media[:8]:
            lines.append(
                f"  {_local_hm(m.first_seen)}  {m.player}: {m.artist or '?'} — {m.title or '?'}"
            )
    if recap.hour_apps:
        lines += ["", "By hour (local, includes away/meeting)"]
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


def _md_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    """GitHub-flavored markdown table (renders in Slack/GitHub/notes)."""
    if not headers:
        return []
    sep = ["---"] * len(headers)
    out = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(sep) + " |",
    ]
    for row in rows:
        cells = list(row) + [""] * max(0, len(headers) - len(row))
        cells = [c.replace("|", "\\|") for c in cells[: len(headers)]]
        out.append("| " + " | ".join(cells) + " |")
    return out


def _short_app(app: str) -> str:
    a = (app or "?").strip()
    aliases = {
        "Google Chrome": "chrome",
        "whatsapp-desktop-linux": "whatsapp",
    }
    return aliases.get(a, a)


def _short_repo(repo: str) -> str:
    r = (repo or "").strip()
    if "/" in r:
        return r.rsplit("/", 1)[-1]
    return r


def _short_title(title: str, *, max_len: int = 28) -> str:
    t = (title or "").strip()
    for suf in (" - grok", " - claude", " - Grok", " - Claude"):
        if t.endswith(suf):
            t = t[: -len(suf)].strip()
            break
    if len(t) <= max_len:
        return t
    return t[: max_len - 1] + "…"
