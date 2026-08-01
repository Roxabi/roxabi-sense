---
title: "P0 focus probes — technical analysis"
description: "Shapes for FocusProbe protocol, DE selection, x11 fallback, runtime demote, doctor capabilities"
issue: 38
issues: [38, 39, 40, 41, 42]
epic: 37
tier: F-full
status: approved
date: 2026-08-01
---

## Source

Epic #37 P0 children: #38 FocusProbe protocol + `source` field · #39 session/DE ordered candidates · #40 x11 probe · #41 runtime fallback when AT-SPI dies · #42 doctor capabilities surface.

Frame: `artifacts/frames/38-p0-focus-probes-frame.md` (approved, F-full, one PR stack).

## Problem

Today focus is AT-SPI-shaped end-to-end:

| Layer | Today | Gap |
|-------|--------|-----|
| Probe | `FocusAtspiAgent` + `probe_once` / injectables on collector | No protocol; cannot swap backend |
| Fact payload | `source: "atspi"` **hardcoded** in `FocusAtspiCollector._maybe_write_focus` / desktop payload | Backend id not pluggable |
| Daemon meta | `last_focus_source` = **acquisition path** (`event`/`cmd`/`backup`/`poll`) | Collides with product `source` (backend id) |
| Failure | AT-SPI exit → poll same AT-SPI one-shot; respawn with backoff | No demote to x11/noop; focus can go silent |
| Doctor | package/db/presence/mcp — no focus/idle **capability** | Operators/agents cannot see backend health |

Also: `collectors/` has **11 files** (gate ≤12). Any P0 that adds peer modules at that level fails the folder-size gate unless we use a **subpackage**.

## Outcome

- One event model for focus: `kind=focus` + `app`/`title`/`pid` + **`source` ∈ {atspi,x11,wlr,kde,noop}** (wlr/kde stubs only as enum room; implementations P1).
- Env-driven ordered probe candidates; first healthy wins; always ends with noop.
- AT-SPI death → next candidate (x11 or noop) without blocking agent_sessions/idle; meta records transition; optional recover when AT-SPI healthy again.
- `sense doctor` (and JSON) surfaces focus + idle capability status + backend id.

## Appetite

One PR stack on `feat/38-p0-focus-probes` — roughly a short F-full cycle (protocol + select + x11 + fallback + doctor), not five separate release trains.

## As-is code map (relevant)

```
collectors/focus_atspi.py   # enrich + dedup + store; hardcoded source=atspi
atspi/agent.py + agent_worker.py  # long-lived process isolation (keep)
daemon.py + daemon_atspi.py # agent lifecycle, respawn, last_focus_source path
doctor.py                   # Check list; no capabilities block
report/status.py + query.py # consumers of store only
```

Idle already has a **chain-like** pattern (wayland watch primary, logind demoted) — good analog for focus demote, but idle is two writers; focus should stay **one collector + probes** (ADR-001).

## Shapes

**Diagram:** [P0 focus shapes](../visuals/38-p0-focus-probes-shapes.html)

### Shape 1: `collectors/focus/` package (recommended)

New package under primary axis:

```
collectors/focus/
  __init__.py          # re-export FocusCollector, select_probe, …
  protocol.py          # FocusProbe Protocol + FocusWindow / get_active
  select.py            # env → ordered candidates + first ok
  session.py           # XDG_SESSION_TYPE / desktop family helpers
  collector.py         # today's FocusAtspiCollector logic (source from probe)
  probes/
    __init__.py
    atspi.py           # wraps FocusAtspiAgent / probe_once
    x11.py             # xprop/xwininfo
    noop.py
```

Compat: `collectors/focus_atspi.py` becomes thin re-export (or delete after import sweep) so tests/daemon keep importing.

Daemon owns **selection + runtime demote/recover**; collector remains pure enrich+write with `source=` from active probe.

**Trade-offs:**
- Pro: folder-size gate clean; clear primary-axis home; P1 wlr/kde drop-in
- Pro: separates protocol/select from AT-SPI process plumbing
- Con: rename/import churn (`FocusAtspiCollector` → `FocusCollector` or keep alias)
- Con: slightly larger first PR than graft

**Rough scope:** L

### Shape 2: Graft next to `focus_atspi.py`

Keep `focus_atspi.py`; add `focus_probe.py`, `focus_select.py`, `focus_x11.py` as siblings under `collectors/`.

**Trade-offs:**
- Pro: minimal rename
- Con: **breaks folder-size ≤12** (already 11 files)
- Con: flat collectors/ becomes focus-heavy; P1 makes it worse
- Con: wrong pressure to “squeeze” instead of package

**Rough scope:** M — **eliminated** by quality gate unless we delete/merge other collectors first (out of scope).

### Shape 3: One collector module per backend

`FocusAtspiCollector`, `FocusX11Collector`, … each writing `kind=focus`.

**Trade-offs:**
- Pro: mirrors naive “module per signal”
- Con: **wrong axis** — backends are variants of **one** signal (focus), not new signal sources (ADR-001 + frame)
- Con: demote/recover becomes multi-collector orchestration; surfaces risk double-count
- Con: duplicates enrich/agent_link

**Rough scope:** L — **eliminated** by ADR-001.

## Fit Check

**Diagram:** [Recommended data flow](../visuals/38-p0-focus-probes-data-flow.html)

**Recommended: Shape 1.**

| Constraint | Shape 1 | Shape 2 | Shape 3 |
|------------|---------|---------|---------|
| ADR-001 primary axis | ✓ probes under focus | ✓ weak | ✗ |
| Folder size ≤12 | ✓ subpackage | ✗ | ✗ pressure |
| AT-SPI isolation preserved | ✓ | ✓ | ✓ |
| Runtime demote | natural | awkward flat | multi-writer mess |
| Doctor capabilities | meta keys clean | ok | messy multi-meta |
| P1 wlr/kde | drop-in probes/ | more flat files | more collectors |

### Naming / meta contract (must fix in Shape 1)

| Key | Meaning |
|-----|---------|
| fact `source` | **backend** `atspi\|x11\|wlr\|kde\|noop` |
| `meta.focus_backend` | active backend id |
| `meta.focus_status` | `available\|degraded\|unavailable` |
| `meta.session_type` | `x11\|wayland\|unknown` |
| `meta.desktop_family` | e.g. `gnome\|cosmic\|kde\|wlroots\|unknown` |
| `meta.last_focus_path` | acquisition path: `event\|cmd\|backup\|poll` (rename from overloaded `last_focus_source` **or** keep old key with documented dual meaning — prefer rename + short compat read in doctor) |

Daemon path labels must not be written into fact `source`.

### Selection matrix (seed for #39)

| Env signals | Candidate order (first `probe().ok`) |
|-------------|--------------------------------------|
| GNOME / COSMIC / a11y-heavy Wayland | atspi → x11 → noop |
| pure X11 (`XDG_SESSION_TYPE=x11`) | x11 → atspi → noop |
| unknown Wayland | atspi → x11 → noop |
| forced / missing DISPLAY & no AT-SPI | noop |

(wlroots/hypr/sway, KDE D-Bus = P1 — reserve enum only.)

### Runtime fallback (#41)

Mirror idle demote pattern, focus-specific:

1. Prefer AT-SPI **event** path when selected and agent healthy.
2. On agent death: mark atspi unhealthy → activate next candidate that `probe().ok` (x11 poll or noop).
3. Set `meta.focus_backend`, `meta.focus_status=degraded` if not first-choice; log transition.
4. Optional: while demoted, keep AT-SPI respawn timer; on ready, re-select first-choice if still preferred.
5. Never raise into daemon main loop; poll collectors always run.

### X11 probe (#40)

- `xprop -root _NET_ACTIVE_WINDOW` → window id → `xprop -id` WM_CLASS / _NET_WM_NAME / _NET_WM_PID
- `probe().ok` false if no `DISPLAY`, bins missing, or commands fail
- Doc line in README: `x11-utils` (or distro equiv)

### Doctor (#42)

- Extend `DoctorReport` with `capabilities: { focus: {status, backend, reason}, idle: {…} }`
- Prefer **meta written by daemon**; if no daemon meta / offline, optional live probe for focus (cheap x11/atspi once) — keep non-blocking
- Text: `[ok] focus: available backend=atspi` / `WARN degraded backend=x11 (atspi dead)`
- JSON: already `to_dict()` — add field without breaking checks array

### Files impacted (expected)

| Path | Change |
|------|--------|
| `src/roxabi_sense/collectors/focus/**` | **new** package (protocol, select, collector, probes) |
| `src/roxabi_sense/collectors/focus_atspi.py` | thin re-export or removed after sweep |
| `src/roxabi_sense/collectors/__init__.py` | export FocusCollector |
| `src/roxabi_sense/daemon.py` | select + demote/recover |
| `src/roxabi_sense/daemon_atspi.py` | pass source=backend; path meta rename |
| `src/roxabi_sense/doctor.py` | capabilities |
| `src/roxabi_sense/report/status.py` / `query.py` | optional: surface backend in status JSON |
| `tests/test_focus_*.py` | protocol fakes, select env matrix, x11 mock, fallback, doctor |
| `README.md` / ARCHITECTURE | matrix + deps |

### Risks

| Risk | Mitigation |
|------|------------|
| Import churn breaks tests | re-export alias + full pytest |
| `last_focus_source` consumers | grep + doctor compat; prefer `last_focus_path` |
| File length ≤300 on daemon | keep demote helpers in `daemon_focus.py` (new) |
| x11 flaky titles | best-effort; same enrich path as AT-SPI |
| Cosmic AT-SPI partial | x11 after atspi fail is the P0 answer |

### Open questions for spec (non-blocking defaults)

1. **Recover to AT-SPI:** yes by default with backoff (issue #41 “optionally”).
2. **Collector class name:** keep `FocusAtspiCollector` as alias forever vs deprecate in one release — recommend alias + new `FocusCollector`.
3. **Status snapshot:** include `focus_backend` in MCP status? Default yes if cheap meta read.

## Expert notes (inline)

- **Architect:** Shape 1 only viable long-term under folder gate + ADR-001; Shape 3 is the classic N×M trap.
- **Product:** Outcome is “focus works + honest doctor on this machine,” not more collector names.
- **Ops:** Meta keys are the SSOT for doctor when daemon is up; live probe only for cold start.

## Recommendation

Proceed to `/spec` on **Shape 1** with meta contract above and commit order:

1. #38 protocol + collector wiring + source field + tests (fake probe)
2. #39 select + session detect + meta
3. #40 X11FocusProbe
4. #41 runtime demote/recover in daemon
5. #42 doctor capabilities
