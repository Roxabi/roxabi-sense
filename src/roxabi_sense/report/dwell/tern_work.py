"""Aggregate independently proved Tern work without inferring human presence."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise

from roxabi_sense.collectors.tern.metadata import opaque
from roxabi_sense.report.dwell.repos import AgentRepoTime, RepoAttribution, repo_label
from roxabi_sense.report.dwell.tern_proofs import proof_bounds
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
    proofs = proof_bounds(snapshots, raw_meta)
    spans: list[tuple[datetime, datetime, str, bool, bool]] = []
    for index, event in enumerate(snapshots):
        if event.payload.get("state") != "observed":
            continue
        lo = max(start, parse_ts(event.ts))
        hi = _next_snapshot_time(snapshots, index, horizon)
        windows = event.payload.get("windows")
        for window in windows if isinstance(windows, list) else []:
            if not isinstance(window, dict):
                continue
            panes = window.get("panes")
            for pane in panes if isinstance(panes, list) else []:
                if not isinstance(pane, dict):
                    continue
                pane_ref = pane.get("pane_ref")
                if not isinstance(pane_ref, str):
                    continue
                ref = opaque(
                    "context", pane_ref, pane.get("session_context_ref"), pane.get("repo_ref")
                )
                pane_proof = pane.get("pane_work_proof_ref")
                pane_bound = proofs.get(pane_proof) if isinstance(pane_proof, str) else None
                if pane.get("pane_working") is not True or pane_bound is None:
                    continue
                pane_hi = _proof_event_end(snapshots, index, pane_proof, hi, horizon, pane_bound)
                begin, end = max(lo, pane_bound[0]), min(pane_hi, pane_bound[1])
                if begin >= end:
                    continue
                spans.append((begin, end, ref, True, False))
                process_proof = pane.get("process_proof_ref")
                process_bound = (
                    proofs.get(process_proof) if isinstance(process_proof, str) else None
                )
                if pane.get("omp_foreground") is True and process_bound is not None:
                    process_hi = _proof_event_end(
                        snapshots, index, process_proof, hi, horizon, process_bound
                    )
                    omp_begin = max(begin, process_bound[0])
                    omp_end = min(end, process_hi, process_bound[1])
                    if omp_begin < omp_end:
                        spans.append((omp_begin, omp_end, ref, False, True))
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
    proofs = proof_bounds(snapshots, raw_meta)
    intervals: dict[tuple[str, str], list[tuple[datetime, datetime, str | None]]] = (
        defaultdict(list)
    )
    labels: dict[tuple[str, str], str] = {}
    coverage_spans = _observed_coverage(coverage or [], start, horizon)
    for index, event in enumerate(snapshots):
        if event.payload.get("state") != "observed":
            continue
        lo = max(start, parse_ts(event.ts))
        hi = _next_snapshot_time(snapshots, index, horizon)
        windows = event.payload.get("windows")
        for window in windows if isinstance(windows, list) else []:
            if not isinstance(window, dict):
                continue
            panes = window.get("panes")
            for pane in panes if isinstance(panes, list) else []:
                if (not isinstance(pane, dict) or pane.get("pane_working") is not True
                        or pane.get("omp_foreground") is not True):
                    continue
                proof = pane.get("pane_work_proof_ref")
                process_proof = pane.get("process_proof_ref")
                cwd = pane.get("cwd")
                bound = proofs.get(proof) if isinstance(proof, str) else None
                process_bound = (
                    proofs.get(process_proof) if isinstance(process_proof, str) else None
                )
                repo_ref = pane.get("repo_ref")
                if isinstance(cwd, str) and cwd and bound is not None and process_bound is not None:
                    begin = max(lo, bound[0], process_bound[0])
                    pane_hi = _proof_event_end(snapshots, index, proof, hi, horizon, bound)
                    process_hi = _proof_event_end(
                        snapshots, index, process_proof, hi, horizon, process_bound
                    )
                    end = min(pane_hi, process_hi, bound[1], process_bound[1])
                    if begin >= end:
                        continue
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
                    intervals[identity].append((begin, end, ref))
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


def _next_snapshot_time(snapshots: list[Event], index: int, horizon: datetime) -> datetime:
    if index + 1 >= len(snapshots):
        return horizon
    event = snapshots[index + 1]
    published = event.payload.get("_published_at")
    if isinstance(published, str):
        try:
            return min(horizon, parse_ts(published))
        except ValueError:
            pass
    return min(horizon, parse_ts(event.ts))


def _proof_event_end(
    snapshots: list[Event], index: int, ref: object, event_end: datetime,
    horizon: datetime, bound: tuple[datetime, datetime],
) -> datetime:
    """Honor a precise closure when its transition timestamp was rounded down."""
    if index + 1 < len(snapshots):
        event = snapshots[index + 1]
        closed = event.payload.get("closed")
        if (not isinstance(event.payload.get("_published_at"), str)
                and event_end <= bound[1] < event_end + timedelta(seconds=1)
                and isinstance(closed, list) and any(
                    isinstance(item, dict) and item.get("proof_ref") == ref for item in closed
                )):
            return min(horizon, bound[1])
    return event_end


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
