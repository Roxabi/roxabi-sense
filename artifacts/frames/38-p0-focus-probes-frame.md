---
title: "P0 focus probes — FocusProbe + detect + x11 + fallback + doctor"
issue: 38
issues: [38, 39, 40, 41, 42]
epic: 37
status: approved
tier: F-full
date: 2026-08-01
---

## Problem

Focus collection is **AT-SPI-centric**: long-lived agent + hardcoded `source: "atspi"` on facts, with poll-only degradation when the agent fails to start. That is enough on some GNOME/Cosmic setups when AT-SPI is healthy, but it is the wrong shape for multi-Linux:

1. **No swappable probe** — DE/session backends cannot be added without forking collectors or surfaces (ADR-001: probes under the focus signal source).
2. **No session/DE selection** — environment (`XDG_SESSION_TYPE`, desktop family) is not mapped to an ordered candidate list.
3. **Weak fallback** — when AT-SPI dies or is incomplete (XWayland/Cosmic gaps), there is no x11 (`xprop`/`xwininfo`) path; focus goes silent or poll-stuck while agent_sessions keep ticking.
4. **Opaque capabilities** — operators and MCP hosts cannot see *this machine’s* focus/idle backend + health (`available` / `degraded` / `unavailable`).

P0 (epic #37 children #38→#42) is the foundation so later wlr/kde probes and aggregates plug in without re-architecture.

## Who

- **Primary:** workstation operator (Pop Cosmic / multi-DE Linux) — needs reliable local focus facts + honest `sense doctor`.
- **Secondary:** host agents via MCP — need machine-readable capabilities / status, not silent empty focus.
- **Not:** factory Sentinelle policy, Promethee product, cloud SaaS.

## Constraints

- **Primary axis (ADR-001):** signal source / collector — FocusProbe lives under the focus collector plane; no host-named package forks; surfaces stay store adapters.
- **Facts only** — collectors emit facts; no Discord/jobs/policy.
- **Existing paths:** keep AT-SPI process isolation (`atspi/` system Python + gi); do not expand the worker into the uv package.
- **Naming collision:** product fact field `source` = backend id (`atspi|x11|wlr|kde|noop`). Daemon today overloads `last_focus_source` with acquisition path (`event|cmd|backup`) — must not conflate; either keep path under a different meta key or document dual meaning clearly.
- **Delivery:** one branch / one PR stack covering #38→#42; release on `main`.
- **Deps order:** protocol (#38) → detect (#39) → x11 (#40) → runtime fallback (#41) → doctor (#42).

## Out of Scope

- wlroots / KDE probes (#43, #44) — P1
- Idle backend chain rewrite (#45) — P1 (doctor may *report* current idle meta only)
- Probe selection fixture matrix as separate ship (#46) — P1 unless trivial unit fixtures land with #39
- Local aggregates `top_apps` (#47) — P2
- Multi-agent RO, NATS coarse — later epic phases
- OCR / screenshots / keylogging
- Per-host package forks

## Premise Validity

**Success in 6 months:** On Pop Cosmic (and documented GNOME / pure X11 paths), focus works via the selected probe; `sense doctor` shows focus backend + capability status; killing the AT-SPI path continues focus via x11 or clear noop with updated meta — without crashing the daemon or blocking other collectors.

**Failure in 6 months:** On the primary workstation (Pop Cosmic), focus stays stuck/noop for ≥7 days while doctor reports healthy (or omits backend), with no usable x11 fallback — silent degradation that agents trust.

**Simplest alternative:** Keep AT-SPI-only + document “install a11y deps on Cosmic.”
**Why not simplest:** Epic #37 and P0 acceptance require multi-backend selection, x11 fallback when AT-SPI is dead/incomplete, and a capabilities surface for operators/MCP. Hardcoding Cosmic does not unblock pure X11 or honest degraded state.

## Complexity

**Tier: F-full** — new FocusProbe protocol (arch pattern), multi-module (probes + detect + collector wiring + daemon fallback + doctor), runtime state machine, meta contract, test surface with env fixtures.

Signals:

- Multiple domains: collectors, daemon orchestration, doctor/CLI surface
- New abstraction (`FocusProbe`) + selection matrix
- Runtime fallback / recovery (not a pure pure-library change)
- Five linked issues, one PR stack

## Delivery shape (agreed)

| | |
|--|--|
| Branch | `feat/38-p0-focus-probes` (worktree) |
| Issues closed by PR | #38, #39, #40, #41, #42 |
| Epic | #37 (P0 phase) |
| Order of commits | protocol → detect → x11 → fallback → doctor |

## Target shape (sketch for analyze/spec — not approved design)

```
collectors/focus/          # or keep focus_atspi + probes package
  probe.py                 # FocusProbe protocol + FocusSnapshot
  select.py                # env → ordered candidates
  probes/
    atspi.py               # wraps existing agent / probe_once
    x11.py                 # xprop/xwininfo
    noop.py
  collector.py             # enrich + store (today's FocusAtspiCollector logic)
daemon: select probe, respawn/fallback, meta.focus_backend
doctor: capabilities block from meta (+ live probe when offline)
```

Fact shape (unchanged kind, explicit backend):

```json
{"kind":"focus","app":"…","title":"…","pid":1234,"source":"atspi|x11|noop"}
```
