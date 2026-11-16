"""Aggregate independently proved Tern work without inferring human presence."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise

from roxabi_sense.collectors.tern.metadata import opaque
from roxabi_sense.report.dwell.repos import AgentRepoTime, RepoAttribution, repo_label
from roxabi_sense.report.dwell.tern_proofs import proof_ends
from roxabi_sense.report.segments import FocusSegment
from roxabi_sense.store import Event
from roxabi_sense.util.time import parse_ts


@dataclass(frozen=True)
class TernWork:
    pane_working_s: float = 0.0
    omp_working_s: float = 0.0
    attended_s: float = 0.0
    unattended_s: float = 0.0
    unknown_attention_s: float = 0.0


def summarize_tern_work(snapshots: list[Event], raw_meta: str | None,
                        segments: list[FocusSegment], start: datetime,
                        horizon: datetime, *, coverage: list[Event] | None = None) -> TernWork:
    """Union shared pane views, classifying OMP work only against known focus."""
    ends = proof_ends(snapshots, raw_meta)
    spans: list[tuple[datetime, datetime, str, bool, bool]] = []
    for index, event in enumerate(snapshots):
        if event.payload.get("state") != "observed":
            continue
        lo = max(start, parse_ts(event.ts))
        hi = min(
            horizon, parse_ts(snapshots[index + 1].ts) if index + 1 < len(snapshots) else horizon
        )
        windows = event.payload.get("windows")
        for window in windows if isinstance(windows, list) else []:
            if not isinstance(window, dict):
                continue
            panes = window.get("panes")
            for pane in panes if isinstance(panes, list) else []:
                if not isinstance(pane, dict):
                    continue
                proof, pane_ref = pane.get("work_proof_ref"), pane.get("pane_ref")
                end = ends.get(proof) if isinstance(proof, str) else None
                if not isinstance(pane_ref, str) or end is None or min(hi, end) <= lo:
                    continue
                pane_busy = pane.get("pane_working") is True
                omp_busy = pane.get("omp_working") is True
                if pane_busy or omp_busy:
                    ref = opaque(
                        "context", pane_ref, pane.get("session_context_ref"), pane.get("repo_ref")
                    )
                    spans.append((lo, min(hi, end), ref, pane_busy, omp_busy))
    bounds = {point for lo, hi, *_ in spans for point in (lo, hi)}
    for segment in segments:
        bounds.update((max(start, parse_ts(segment.start)), min(horizon, parse_ts(segment.end))))
    coverage_spans = _observed_coverage(coverage or [], start, horizon)
    bounds.update(point for lo, hi in coverage_spans for point in (lo, hi))
    points = sorted(point for point in bounds if start <= point <= horizon)
    pane_total = omp_total = attended = unattended = unknown = 0.0
    for lo, hi in pairwise(points):
        active = [s for s in spans if s[0] <= lo and hi <= s[1]]
        duration = (hi - lo).total_seconds()
        if any(s[3] for s in active):
            pane_total += duration
        omp = [s for s in active if s[4]]
        if not omp:
            continue
        omp_total += duration
        focused = next(
            (s for s in segments if parse_ts(s.start) <= lo and hi <= parse_ts(s.end)), None
        )
        if (focused is None or not _covered(coverage_spans, lo, hi)
                or (focused.app == "so.stencil.tern" and not focused.context_ref)):
            unknown += duration
        elif any(s[2] == focused.context_ref for s in omp):
            attended += duration
        else:
            unattended += duration
    return TernWork(pane_total, omp_total, attended, unattended, unknown)


def add_tern_work(
    repos: RepoAttribution, snapshots: list[Event], raw_meta: str | None,
    segments: list[FocusSegment], start: datetime, horizon: datetime,
    *, coverage: list[Event] | None = None,
) -> RepoAttribution:
    ends = proof_ends(snapshots, raw_meta)
    intervals: dict[tuple[str, str], list[tuple[datetime, datetime, str | None]]] = (
        defaultdict(list)
    )
    labels: dict[tuple[str, str], str] = {}
    coverage_spans = _observed_coverage(coverage or [], start, horizon)
    for index, event in enumerate(snapshots):
        if event.payload.get("state") != "observed":
            continue
        lo = max(start, parse_ts(event.ts))
        hi = min(
            horizon, parse_ts(snapshots[index + 1].ts) if index + 1 < len(snapshots) else horizon
        )
        windows = event.payload.get("windows")
        for window in windows if isinstance(windows, list) else []:
            if not isinstance(window, dict):
                continue
            panes = window.get("panes")
            for pane in panes if isinstance(panes, list) else []:
                if not isinstance(pane, dict) or pane.get("omp_working") is not True:
                    continue
                proof = pane.get("work_proof_ref")
                cwd = pane.get("cwd")
                end = ends.get(proof) if isinstance(proof, str) else None
                repo_ref = pane.get("repo_ref")
                if isinstance(cwd, str) and cwd and end is not None and min(hi, end) > lo:
                    # Older snapshots lack repo identity; only the same proof is shared.
                    identity = (("repo", repo_ref) if isinstance(repo_ref, str) and repo_ref
                                else ("proof", str(proof)))
                    labels.setdefault(identity, repo_label(cwd))
                    pane_ref = pane.get("pane_ref")
                    ref = (
                        opaque(
                            "context",
                            pane_ref,
                            pane.get("session_context_ref"),
                            pane.get("repo_ref"),
                        )
                        if isinstance(pane_ref, str) else None
                    )
                    intervals[identity].append((lo, min(hi, end), ref))
    agents = list(repos.agents)
    for identity, spans in intervals.items():
        merged: list[tuple[datetime, datetime]] = []
        for lo, hi, _ in sorted(spans, key=lambda span: (span[0], span[1])):
            if merged and lo <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
            else:
                merged.append((lo, hi))
        working = sum((hi - lo).total_seconds() for lo, hi in merged)
        unattended = 0.0
        for segment in segments:
            if segment.app == "so.stencil.tern" and not segment.context_ref:
                continue
            a, b = parse_ts(segment.start), parse_ts(segment.end)
            points = sorted({a, b} | {max(a, lo) for lo, hi, _ in spans if lo < b and hi > a}
                            | {min(b, hi) for lo, hi, _ in spans if lo < b and hi > a}
                            | {max(a, lo) for lo, hi in coverage_spans if lo < b and hi > a}
                            | {min(b, hi) for lo, hi in coverage_spans if lo < b and hi > a})
            for lo, hi in pairwise(points):
                active = [ref for begin, end, ref in spans if begin <= lo and hi <= end]
                if (active and _covered(coverage_spans, lo, hi)
                        and (segment.context_ref is None or segment.context_ref not in active)):
                    unattended += (hi - lo).total_seconds()
        agents.append(
            AgentRepoTime(repo=labels[identity], working_s=working, unfocused_s=unattended)
        )
    return RepoAttribution(repos.focus, agents, repos.focused_repo_now)


def _observed_coverage(
    coverage: list[Event], start: datetime, horizon: datetime,
) -> list[tuple[datetime, datetime]]:
    """Only native observed coverage can establish known human attention."""
    result = []
    for index, event in enumerate(coverage):
        if event.payload.get("state") != "observed":
            continue
        lo = max(start, parse_ts(event.ts))
        hi = min(horizon, parse_ts(coverage[index + 1].ts)
                 if index + 1 < len(coverage) else horizon)
        if lo < hi:
            result.append((lo, hi))
    return result


def _covered(spans: list[tuple[datetime, datetime]], lo: datetime, hi: datetime) -> bool:
    return any(start <= lo and hi <= end for start, end in spans)
