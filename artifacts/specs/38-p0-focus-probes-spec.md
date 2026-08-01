---
title: "P0 focus probes — solution spec"
issue: 38
issues: [38, 39, 40, 41, 42]
epic: 37
tier: F-full
status: approved
date: 2026-08-01
shape: "collectors/focus/ package"
---

## Context

- **Frame:** [38-p0-focus-probes-frame.md](../frames/38-p0-focus-probes-frame.md)
- **Analysis:** [38-p0-focus-probes-analysis.md](../analyses/38-p0-focus-probes-analysis.md) — Shape 1 approved
- **Epic:** #37 P0 · children #38–#42 · one PR stack

## Goal

Ship a swappable **FocusProbe** plane under the focus collector so multi-Linux sessions get reliable focus facts, honest meta, x11 fallback, and a doctor capabilities surface — without host forks or surface reimplementation.

## Users

| Who | Need |
|-----|------|
| Operator (Pop Cosmic / multi-DE) | Focus keeps working; `sense doctor` tells truth about backend |
| Host agents (MCP) | Machine-readable capabilities / status |
| Future P1 (wlr/kde) | Drop-in probe modules |

## Expected Behavior

1. **Boot:** daemon detects session/desktop → builds ordered probe candidates → first `probe().ok` becomes active → writes `meta.focus_backend`, `meta.focus_status`, `meta.session_type`, `meta.desktop_family`.
2. **Steady state (AT-SPI selected):** long-lived agent emits focus events; collector enriches and appends `kind=focus` with `source=atspi`. Desktop backup unchanged. Path meta uses `last_focus_path` (`event`/`cmd`/`backup`/`poll`).
3. **AT-SPI dies:** daemon demotes to next healthy candidate (typically x11 poll); sets `focus_status=degraded` (or `unavailable` if only noop); other collectors keep ticking; optional AT-SPI respawn recovers to preferred backend when healthy.
4. **Pure X11 session:** selection prefers x11 first; AT-SPI optional if available.
5. **Doctor:** shows focus + idle capability rows (status + backend + short reason); JSON includes `capabilities` object. Prefer daemon meta; cold path may live-probe without hanging.
6. **Surfaces:** CLI/MCP continue reading store only; may expose backend via existing status/meta if already loaded.

## Data Model & Consumers

**Data structure:** [Focus data model](../visuals/38-p0-focus-probes-data-model.html)  
**Consumer map:** [Consumers](../visuals/38-p0-focus-probes-consumers.html)

### Types

```python
# protocol (conceptual)
class FocusProbe(Protocol):
    source: Literal["atspi", "x11", "wlr", "kde", "noop"]
    def probe(self) -> bool: ...          # healthy / usable right now
    def get_active(self) -> list[FocusWindow]: ...  # 0..n windows; active marked

@dataclass
class FocusWindow:
    app: str
    title: str
    active: bool
    role: str = ""
    pid: int | None = None
```

### Fact payload (`kind=focus`)

| Field | Type | Notes |
|-------|------|-------|
| `app` | str | enriched |
| `title` | str | normalized |
| `pid` | int? | optional |
| `source` | str | **backend id only** |
| `title_raw` | str? | if normalized differs |
| `agent` | object? | session link |

Desktop snapshot `source` likewise uses backend id.

### Meta keys (daemon SSOT)

| Key | Values |
|-----|--------|
| `focus_backend` | `atspi\|x11\|wlr\|kde\|noop` |
| `focus_status` | `available\|degraded\|unavailable` |
| `session_type` | `x11\|wayland\|unknown` |
| `desktop_family` | `gnome\|cosmic\|kde\|wlroots\|unknown` (+ documented aliases) |
| `last_focus_path` | `event\|cmd\|backup\|poll` |
| `atspi_agent` | keep existing `starting\|ready\|dead\|n/a` |

Compat: if any reader still checks `last_focus_source`, doctor/status may mirror path into both during transition **or** grep-replace all in-repo uses in same PR (prefer replace-in-repo; no public API promise on meta keys).

### Consumer summary

| Consumer | Fields | When | Status |
|----------|--------|------|--------|
| Store / day / segments | focus facts app/title/ts | report | this PR (unchanged logic) |
| `active_now` / query | latest focus (+ optional source) | MCP | this PR optional source pass-through |
| Doctor | meta + capabilities | operator | **this PR** |
| Status snapshot | meta keys | CLI/MCP | this PR if already reads meta |
| NATS | — | — | future |
| wlr/kde probes | protocol | P1 | future |

## Breadboard

| ID | Affordance | Handler | Data |
|----|------------|---------|------|
| N1 | `FocusProbe` protocol | `collectors/focus/protocol.py` | FocusWindow, source enum |
| N2 | Session detect | `session.py` | env → session_type, desktop_family |
| N3 | Ordered candidates | `select.py` | list[probe factories] |
| N4 | AtspiFocusProbe | `probes/atspi.py` | wraps agent / probe_once |
| N5 | X11FocusProbe | `probes/x11.py` | xprop/xwininfo |
| N6 | NoopFocusProbe | `probes/noop.py` | always “ok”, empty get_active |
| N7 | FocusCollector | `collector.py` | enrich + append with probe.source |
| N8 | Compat re-export | `focus_atspi.py` | FocusAtspiCollector alias |
| N9 | Daemon select + demote | `daemon_focus.py` + `daemon.py` | meta writes, poll path |
| N10 | Doctor capabilities | `doctor.py` | capabilities block |
| N11 | Tests | `tests/test_focus_*.py` | fake probe, env matrix, x11 mock, demote, doctor |
| S1 | README / ARCHITECTURE | docs | selection matrix + x11-utils |

### Wiring

```
env → N2 → N3 → (N4|N5|N6).probe ok
N4 events | N5 poll | N6 empty → N7 → store
N9 orchestrates N4 lifecycle + demote → meta
N10 reads meta (+ optional live N5/N4)
```

## Slices

| Slice | Issues | Demo | Affords |
|-------|--------|------|---------|
| **S1 Protocol + collector** | #38 | Fake probe writes focus with `source=fake`; unit tests | N1, N7, N8, N11 |
| **S2 Select + meta** | #39 | Env fixtures → deterministic candidate order; meta written | N2, N3, N9 partial, N11 |
| **S3 X11 probe** | #40 | Mock subprocess → app/title/pid; probe false without DISPLAY | N5, N11, S1 |
| **S4 Runtime fallback** | #41 | Simulated AT-SPI death → x11/noop meta transition; other ticks continue | N9 full, N11 |
| **S5 Doctor capabilities** | #42 | `sense doctor` / JSON shows focus+idle backend+status | N10, N11 |

Ship order = S1→S5 in one branch; each slice independently testable.

## Selection matrix (normative for S2)

| session_type | desktop_family hints | Order |
|--------------|----------------------|-------|
| wayland | gnome, cosmic, unity, … | atspi → x11 → noop |
| x11 | * | x11 → atspi → noop |
| wayland | kde | atspi → x11 → noop *(kde probe P1)* |
| wayland | hyprland, sway, wlroots | atspi → x11 → noop *(wlr P1)* |
| unknown | * | atspi → x11 → noop |

Always terminate with **noop**. First `probe() is True` wins.

## Success Criteria

### Protocol & facts (#38)

- [ ] `FocusProbe` protocol exists with `source`, `probe() -> bool`, `get_active() -> list[FocusWindow]`
- [ ] New focus events include `source` matching the active probe’s backend id (not event/cmd/poll)
- [ ] Collector accepts an injected probe; unit tests with fake probe pass without AT-SPI
- [ ] Surfaces do not reimplement focus collection (no new SQL under surfaces/)

### Selection (#39)

- [ ] Given env fixtures, candidate order matches the selection matrix
- [ ] On selection, store receives `meta.focus_backend`, `meta.session_type`, `meta.desktop_family`
- [ ] Matrix for Cosmic / GNOME / pure X11 / unknown documented in README or ARCHITECTURE

### X11 (#40)

- [ ] `X11FocusProbe` returns app + title + pid when DISPLAY set and tools present (mocked in tests)
- [ ] `probe()` is False when no DISPLAY or bins missing
- [ ] When selected, focus facts have `source=x11`
- [ ] README notes package dep (`x11-utils` or distro equivalent)

### Runtime fallback (#41)

- [ ] On AT-SPI unhealthy, daemon switches to next healthy candidate without process crash
- [ ] Meta shows backend transition (`focus_backend`, `focus_status`); log line mentions transition
- [ ] agent_sessions / idle poll path still ticks while focus demoted
- [ ] Optional recover: when AT-SPI becomes healthy again and was preferred, backend returns to atspi (tested or documented if deferred — **default: implement**)

### Doctor (#42)

- [ ] `sense doctor` text includes focus capability: status + backend + short reason
- [ ] Idle capability row present (from existing idle meta / best-effort)
- [ ] JSON report includes `capabilities` with at least `focus` and `idle` objects
- [ ] Prefer daemon meta; does not hang if live probe used offline

### Cross-cutting

- [ ] `collectors/` root stays ≤12 files (new code under `collectors/focus/`)
- [ ] File length ≤300 for new/edited modules (split `daemon_focus.py` if needed)
- [ ] `uv run pytest` · `ruff` · `pyright` green
- [ ] No host-named package forks; no OCR/screenshots

## Edge cases

| Case | Handling |
|------|----------|
| No DISPLAY, AT-SPI dead | noop; `focus_status=unavailable` |
| xprop missing | x11 probe false; try next |
| Unnamed window apps | existing `resolve_app_name` enrich |
| Desktop snapshot during x11 | best-effort: single active window list or skip desktop inventory if probe focus-only |
| Config `focus=false` | no focus collector; doctor reports unavailable/disabled |
| Concurrent demote + poll tick | single active probe reference; store batch as today |

## Out of scope (restate)

wlr/kde implementations · idle chain rewrite · top_apps aggregates · NATS · multi-agent RO · #46 full matrix as separate product (minimal fixtures in S2 OK)

## Defaults (resolved)

| Topic | Decision |
|-------|----------|
| Recover to AT-SPI | Yes, with existing backoff style |
| Class name | `FocusCollector` + `FocusAtspiCollector` alias |
| Status/MCP source field | Pass through on latest focus if already returning payload fields |
| `last_focus_source` | Rename to `last_focus_path` in-repo; no dual-write required if all greps fixed |

## Pre-check

| Check | Result |
|-------|--------|
| Testable criteria | pass — binary checkboxes |
| No dangling breadboard IDs | pass — all N*/S* in slices |
| Ambiguity budget | pass — 0 χ |
| Slice coverage | pass |
| Edge completeness | pass |
