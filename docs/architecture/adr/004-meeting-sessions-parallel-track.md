---
title: "ADR-004: Meeting sessions as a parallel attention track"
description: Observed browser call evidence and inferred window signals on a parallel track
status: accepted
date: 2026-08-03
relates_to:
  - 001-axis-of-decomposition
  - 002-presence-and-idle
---

## Status

Accepted

## Context

Focus, meeting activity and input presence describe different things. Multitasking
during a call must not erase focus dwell; input silence is not proof of a call.
Desktop titles and window inventory can suggest a meeting, but neither a room
URL nor a multi-window inventory proves participation or source health.

## Decision

Meeting sessions remain a parallel report track, separate from focus and
presence. The opt-in Chrome Google Meet connector records scoped observations
of recognized UI states. A `joined` observation means an in-call leave control
was observed, not server acknowledgement or human attention. Recognized
home, prejoin and postcall states are `not_joined`; ambiguous, unsupported or
unresponsive states are unknown. No title, URL, microphone or calendar fallback
establishes an observed call. Real joined and reconnecting provider layouts have
not yet been validated in a real call.

Historical `desktop_snapshot` and focus titles remain **inferred** evidence,
bounded to 300 seconds after the last supporting sample. They are not promoted
to observed evidence, and stored legacy events are not rewritten.

### Time and coverage

* `meeting_total_s` is the union of observed joined intervals only.
  `meeting_inferred_s` is inferred call time excluding the observed union.
  Sessions expose `evidence` as `observed` or `inferred`. Observed union
  components are evidence segments, not physical calls: gaps can split one
  call and concurrent calls can merge.
* Confirmed intervals require contiguous observations within eight seconds.
  Unknown, missing, stale or interrupted observations stop at the last
  confirmed point; a single joined observation contributes no historical
  duration. A fresh live annotation can still say `in_call`.
* Coverage is scoped to all registered Chrome Google Meet sources, not all
  workstation meeting applications. A complete healthy empty enumeration is
  known no calls; absent registration, missing sources and unknown tabs are
  unknown, not proof of no calls. Conflicted sources invalidate coverage until
  explicitly removed and reinstalled; a healthy source can still provide a
  positive lower bound. Recaps expose `meeting_coverage_s`,
  `meeting_unknown_s`, `meeting_quality` and event-window truncation. A truncated
  prefix cannot certify the unseen suffix or extend focus and meeting totals
  beyond defensible witnesses; this differs from JSON output-size truncation.
* Focus and observed calls can overlap. `focus_in_meeting_s` and
  `focus_outside_meeting_s` describe that overlap; `activity_union_s` is their
  union with observed call time, not the sum of parallel tracks. Concurrent
  calls and hourly overlays are unioned.

Live annotation defaults to unknown. Only fresh observed joined evidence gives
`phase=in_call`; legacy classification is exposed separately as
`inferred_phase`. Known not-joined and unknown are distinct. CLI, MCP and
other surfaces consume the same report products rather than reclassifying
window titles.

### Collection and privacy

The browser connector is opt-in. It emits host-timestamped snapshots containing
only scoped tab state and its recognition basis; it does not collect URLs,
titles, meeting codes, participants, DOM text, audio or screenshots. Source,
connection and tab identifiers are local opaque correlation keys and must not
appear in coarse exports or public meeting session data. A failed enumeration,
transport loss or sequence gap invalidates coverage rather than synthesizing a
leave. Collector facts remain separate from report interpretation.

Native-host registration applies to the browser user-data directory, not an
authenticated profile; either the supplied profile path or explicit data-root
path locates that registration.
Multiple profiles using the same extension ID can contend for one source. Such
a conflict is latched as unknown rather than aging into a healthy state. Stop
duplicate producers and complete owned removal and reinstallation to recover.
The registered launcher pins the invoking installation, so remove and
re-register before moving or uninstalling that executable or its environment.
See the [operator sequence](../../../README.md#meeting-evidence).

## Consequences

The observed total is a conservative lower bound, not a calendar duration or a
measure of attention. The connector's scope and unknown periods must accompany
comparisons. Title-derived estimates remain useful but separate; improved
desktop inventory does not make them observed. Provider UI changes can reduce
coverage, and unsupported or reconnecting layouts remain unknown.

## References

* ADR-001 Axis of Decomposition
* ADR-002 Presence and idle
* Issue #115, browser meeting evidence
