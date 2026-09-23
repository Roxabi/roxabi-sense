"""Per-repo attribution: human focus vs agent progress (facts only, no priorities).

Agent time is also split per session (Herdr ``session_id`` + session ``title``) so a
consumer can map work to a task, and ``now`` carries each session's live Herdr status
(``working`` / ``blocked`` / ``done`` / ``idle`` / ``unknown``).

- **Focus** (you are on it): OS focus on a terminal × the repo of Herdr's focused
  pane over the same interval. Herdr's server focus is authoritative; Ghostty window
  titles go stale across multi-client views and bare process ``comm`` picks whatever
  long-lived agent it finds first (#79).
- **Agent time** (it moves forward): wall time with at least one Herdr pane in the
  repo reporting ``working`` — counted whether or not you look at it; ``unfocused_s``
  is the part while your focus was elsewhere (another repo, another app, or away).

A snapshot is trusted for at most ``MAX_HOLD_S``: the collector re-emits a keyframe
every 300 s, so an older state means the daemon was down or the machine suspended.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from roxabi_sense.report.segments import FocusSegment, is_terminal_app
from roxabi_sense.store import Event
from roxabi_sense.util.time import parse_ts

HERDR_KIND = "herdr_snapshot"
MAX_HOLD_S = 900.0


@dataclass(frozen=True)
class AgentSessionTime:
    session_id: str  # OMP/agent session id (Herdr pane id when the agent exposes none)
    title: str  # agent session title (dropped by coarse redaction)
    working_s: float
    now: str | None = None  # Herdr status at the recap horizon, None when not live


@dataclass(frozen=True)
class AgentRepoTime:
    repo: str
    working_s: float
    unfocused_s: float
    # Pane count per Herdr status at the recap horizon; empty when no live snapshot.
    now: dict[str, int] = field(default_factory=dict)
    sessions: list[AgentSessionTime] = field(default_factory=list)


@dataclass(frozen=True)
class RepoAttribution:
    focus: list[tuple[str, float]]
    agents: list[AgentRepoTime]
    focused_repo_now: str | None


@dataclass(frozen=True)
class _Pane:
    repo: str
    status: str
    key: str
    title: str


@dataclass(frozen=True)
class _Span:
    start: datetime
    end: datetime
    focused_repo: str | None
    panes: tuple[_Pane, ...]


def repo_label(cwd: str) -> str:
    """Short, path-free repo label: ``org/repo`` under ``~/projects``, else basename."""
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


def attribute_repos(
    events: list[Event],
    segments: list[FocusSegment],
    *,
    window_start: datetime,
    horizon: datetime,
    prior: Event | None = None,
) -> RepoAttribution:
    """Split the day into focus-per-repo and agent-working-per-repo."""
    snaps = [e for e in events if e.kind == HERDR_KIND]
    spans = _spans([prior, *snaps] if prior is not None else snaps, window_start, horizon)
    if not spans:
        # tmux-only / pre-Herdr hosts: focus-time link, never a bare-comm guess.
        legacy = [s for s in segments if s.cwd and s.agent_match != "comm"]
        totals: dict[str, float] = defaultdict(float)
        for s in legacy:
            totals[repo_label(s.cwd or "")] += s.duration_s
        return RepoAttribution(_ranked(totals), [], None)

    attn = _attention_per_span(spans, segments)
    focus: dict[str, float] = defaultdict(float)
    working: dict[str, float] = defaultdict(float)
    focused_on: dict[str, float] = defaultdict(float)
    session_work: dict[tuple[str, str], float] = defaultdict(float)
    titles: dict[str, str] = {}
    for span, attn_s in zip(spans, attn, strict=True):
        if span.focused_repo and attn_s > 0:
            focus[span.focused_repo] += attn_s
        dur = (span.end - span.start).total_seconds()
        titles.update((p.key, p.title) for p in span.panes if p.title)
        busy = [p for p in span.panes if p.status == "working"]
        for key in {(p.repo, p.key) for p in busy}:
            session_work[key] += dur
        for repo in {p.repo for p in busy}:
            working[repo] += dur
            if repo == span.focused_repo:
                focused_on[repo] += attn_s

    live = spans[-1] if spans[-1].end >= horizon else None
    status_now = {(p.repo, p.key): p.status or "unknown" for p in live.panes} if live else {}
    sessions: dict[str, list[AgentSessionTime]] = defaultdict(list)
    for repo, key in session_work.keys() | status_now.keys():
        sessions[repo].append(
            AgentSessionTime(
                session_id=key,
                title=titles.get(key, ""),
                working_s=session_work.get((repo, key), 0.0),
                now=status_now.get((repo, key)),
            )
        )
    ranked_repos = _ranked(working) + [(r, 0.0) for r in sorted(sessions) if r not in working]
    agents = [
        AgentRepoTime(
            repo=repo,
            working_s=secs,
            unfocused_s=max(0.0, secs - focused_on[repo]),
            now=_status_counts(sessions[repo]),
            sessions=sorted(sessions[repo], key=lambda s: (-s.working_s, s.title)),
        )
        for repo, secs in ranked_repos
    ]
    return RepoAttribution(
        focus=_ranked(focus),
        agents=agents,
        focused_repo_now=live.focused_repo if live is not None else None,
    )


def _status_counts(sessions: list[AgentSessionTime]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for s in sessions:
        if s.now is not None:
            counts[s.now] += 1
    return dict(counts)


def _spans(snaps: list[Event], window_start: datetime, horizon: datetime) -> list[_Span]:
    out: list[_Span] = []
    for i, e in enumerate(snaps):
        t = parse_ts(e.ts)
        nxt = parse_ts(snaps[i + 1].ts) if i + 1 < len(snaps) else horizon
        start = max(t, window_start)
        end = min(nxt, t + timedelta(seconds=MAX_HOLD_S), horizon)
        if end > start:
            out.append(_Span(start, end, _focused_repo(e.payload), _pane_repos(e.payload)))
    return out


def _focused_repo(payload: dict[str, Any]) -> str | None:
    focused = payload.get("focused")
    if isinstance(focused, dict):
        cwd = str(focused.get("cwd") or "")
        return repo_label(cwd) if cwd else None
    # Pre-keyframe snapshots: agent panes only carry the flag.
    hits = [p for p in payload.get("panes") or [] if isinstance(p, dict) and p.get("focused")]
    if len(hits) == 1 and hits[0].get("cwd"):
        return repo_label(str(hits[0]["cwd"]))
    return None


def _pane_repos(payload: dict[str, Any]) -> tuple[_Pane, ...]:
    return tuple(
        _Pane(
            repo=repo_label(str(p["cwd"])),
            status=str(p.get("status") or ""),
            key=str(p.get("session_id") or p.get("pane_id") or ""),
            title=str(p.get("title") or ""),
        )
        for p in payload.get("panes") or []
        if isinstance(p, dict) and p.get("cwd")
    )


def _attention_per_span(spans: list[_Span], segments: list[FocusSegment]) -> list[float]:
    """Seconds of terminal focus inside each span (both lists sorted, disjoint)."""
    terminal = [(parse_ts(s.start), parse_ts(s.end)) for s in segments if is_terminal_app(s.app)]
    out = [0.0] * len(spans)
    j = 0
    for i, span in enumerate(spans):
        while j < len(terminal) and terminal[j][1] <= span.start:
            j += 1
        k = j
        while k < len(terminal) and terminal[k][0] < span.end:
            lo, hi = max(span.start, terminal[k][0]), min(span.end, terminal[k][1])
            if hi > lo:
                out[i] += (hi - lo).total_seconds()
            k += 1
    return out


def _ranked(totals: dict[str, float]) -> list[tuple[str, float]]:
    return sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))
