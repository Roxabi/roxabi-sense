"""Share card and full text renderers for a compiled day recap."""

from __future__ import annotations

from roxabi_sense.report.away import IDLE_GAP_S
from roxabi_sense.report.day import DayRecap
from roxabi_sense.report.meeting_sessions import format_meeting_sessions
from roxabi_sense.report.segments import MIN_DWELL_S
from roxabi_sense.util.time import parse_ts


def format_day_recap_share(
    recap: DayRecap,
    *,
    max_rank: int = 4,
) -> str:
    """Shareable day card: at most **two** Markdown tables.

    1. Day overview (metrics + switches + terminal in one key/value table)
    2. Ranked tops (app · repo · window side by side)
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
    ts = recap.terminal_stays
    if ts.visits:
        term = (
            f"{ts.visits} vis · med {_fmt_dur(ts.median_s)} · "
            f"avg {_fmt_dur(ts.mean_s)} · "
            f"≥2m {ts.ge_2m} ({_fmt_dur(ts.time_ge_2m_s)}) · "
            f"≥5m {ts.ge_5m} ({_fmt_dur(ts.time_ge_5m_s)}) · "
            f"≥10m {ts.ge_10m}"
        )
    else:
        term = "—"

    overview = _md_table(
        ["metric", "value"],
        [
            ["day", f"{recap.day} · {window} · {shape}"],
            ["focus / away", f"{_fmt_dur(tracked)} / {_fmt_dur(recap.away_total_s)}"],
            ["meet / agents", f"{meet} / {len(recap.agent_sessions)}"],
            [
                "switches",
                (
                    f"{recap.focus_switches_app} app · "
                    f"{recap.focus_switches_context} ctx · "
                    f"{recap.focus_switches} title"
                ),
            ],
            ["terminal", term],
        ],
    )

    # Parallel rank columns: app | repo | top window
    apps: list[tuple[str, str]] = []
    if recap.top_apps:
        apps = [
            (_short_app(a.app), f"{_fmt_dur(a.seconds)} ({a.share * 100:.0f}%)")
            for a in recap.top_apps[:max_rank]
        ]
    elif recap.time_by_app:
        total = sum(s for _, s in recap.time_by_app) or 1.0
        apps = [
            (_short_app(n), f"{_fmt_dur(s)} ({s / total * 100:.0f}%)")
            for n, s in recap.time_by_app[:max_rank]
        ]
    repos: list[tuple[str, str]] = []
    if recap.time_by_repo:
        rtot = sum(s for _, s in recap.time_by_repo) or 1.0
        repos = [
            (_short_repo(r), f"{_fmt_dur(s)} ({s / rtot * 100:.0f}%)")
            for r, s in recap.time_by_repo[:max_rank]
        ]
    titles: list[tuple[str, str]] = [
        (_short_title(t, max_len=32), _fmt_dur(s))
        for t, s, _a in recap.top_titles[:max_rank]
    ]

    n = max(len(apps), len(repos), len(titles), 0)
    blocks = [f"**sense · {recap.day}**", "", *overview]
    if n == 0:
        return "\n".join(blocks)

    rank_rows: list[list[str]] = []
    for i in range(n):
        a_name, a_t = apps[i] if i < len(apps) else ("", "")
        r_name, r_t = repos[i] if i < len(repos) else ("", "")
        t_name, t_t = titles[i] if i < len(titles) else ("", "")
        rank_rows.append(
            [
                str(i + 1),
                f"{a_name} {a_t}".strip(),
                f"{r_name} {r_t}".strip(),
                f"{t_name} {t_t}".strip(),
            ]
        )
    blocks.append("")
    blocks.extend(_md_table(["#", "app", "repo", "top window"], rank_rows))
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
            f"avg {_fmt_dur(ts.mean_s)} · "
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
        lines += ["", "Repos in focus (terminal × herdr focused pane)"]
        repo_total = sum(s for _, s in recap.time_by_repo)
        for repo, secs in recap.time_by_repo[:12]:
            pct = (secs / repo_total * 100) if repo_total else 0
            lines.append(f"  {_pad(repo, 28)} {_fmt_dur(secs):>8}  {pct:5.1f}%")
    if recap.agent_time_by_repo:
        lines += ["", "Agents working by repo (total · while not in your focus)"]
        for a in recap.agent_time_by_repo[:12]:
            now = " ".join(f"{k}={v}" for k, v in sorted(a.now.items()))
            lines.append(
                f"  {_pad(a.repo, 28)} {_fmt_dur(a.working_s):>8}  "
                f"{_fmt_dur(a.unfocused_s):>8}  {now}".rstrip()
            )
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
