---
title: "Goal — Presence, idle, surfaces (phases 1→2→3)"
status: active
date: 2026-07-30
repo: roxabi-sense
axis: collectors (ADR-001)
---

# Goal

Ship an honest **attention presence** pipeline on Cosmic/Linux:

**Source → Capteur → Logger → Vue**, without becoming screenpipe.

## Objective (done when)

An operator can answer, locally:

1. **What was I doing today?** — compiled day recap (not raw firehose)
2. **Am I active / idle / offline right now?** — honest status from real signals
3. **Can an agent / factory read the same truth thinly?** — MCP local, NATS coarse later

…using **facts only** in collectors, **one SQLite logger**, **thin surfaces**.

## Architecture (fixed)

```
SOURCE (OS)          CAPTEUR (collectors/)       LOGGER (store)         VUE (report/cli/mcp/nats)
─────────────────    ──────────────────────      ───────────────        ────────────────────────
clavier/souris   →   idle_watch (Wayland)    →   events idle        →   status / recap
fenêtre active   →   focus AT-SPI            →   events focus       →   recap dwell
Spotify/…        →   mpris                   →   media_snapshot     →   recap exception
~/.grok / claude →   agent_sessions          →   agent snapshots    →   recap sessions
process names    →   process_presence        →   process_snapshot   →   status “apps present”
daemon vivant    →   coordinator meta*       →   meta last_tick     →   status offline?

* not a peer “fact collector” — daemon coordinator (ADR cross-collector debt)
```

### Hard rules

- Collectors = **faits purs** (no Discord, jobs, Sentinelle policy)
- No OCR / screenshots / keylogging
- Heartbeat **not** a firehose in `events` (meta liveness or transition-only)
- State machine `active|idle|offline` = **coordinator + shared report/store rules**, not per-collector
- NATS = `activity` / `stale` only, no title firehose
- MCP defaults to **redacted/coarse** when shipped
- Idle authority runtime: **ext-idle-notify** > logind > degraded gap (compile-only fallback)

---

## Phase 1 — Logger + baseline recap (foundation)

**Intent:** Land the day compiler and degraded away; freeze Source→Capteur→Logger mental model.

### Deliverables

| ID | Deliverable | Done when |
|----|-------------|-----------|
| 1.1 | `report/` day recap (`compile_day_recap`, `format_day_recap`) | `sense recap` works on live DB |
| 1.2 | CLI `sense recap [--date] [--json]` | help + tests green |
| 1.3 | Degraded away: gap ≥ 5 min on `focus`+`desktop_snapshot`; start = last activity | recap shows Away section; focus dwell cut |
| 1.4 | Tests for recap + gap semantics | pytest green |
| 1.5 | Ship baseline: commit + PR if not already on main | CI green; `uv tool install -e .` |

### Non-goals (phase 1)

- Wayland idle protocol
- MCP / NATS
- Heartbeat rows

### Verify

```bash
uv run pytest -q
sense recap | head -40   # Away + Focus time present
```

**Status note:** Code largely exists uncommitted on workspace (`report/`, `cli.py`, tests) — phase 1 = **merge & reinstall**, not redesign.

---

## Phase 2 — Capteur idle + status honnête

**Intent:** Real input idle on Cosmic + daemon liveness without store bloat.

### Deliverables

| ID | Deliverable | Done when |
|----|-------------|-----------|
| 2.0 | ADR short `presence/idle` (authority, SM placement, no heartbeat firehose) | ADR accepted under `docs/architecture/adr/` |
| 2.1 | Meta liveness: `last_tick` / presence fields; **no** 15–30s event spam | `sense status` shows age; DB growth ~flat vs today |
| 2.2 | `idle_watch` subprocess (ext-idle-notify, prefer `get_input_idle_notification` 300s) | transitions `idle` enter/leave with `source`, `idle_since=last_activity` |
| 2.3 | Daemon: start/stop/respawn watch; crash ≠ kill agents; fallback degraded flag | kill watch → log + degrade; restart recovers |
| 2.4 | Idle authority: Wayland primary; logind demoted/config | dual-write not conflicting in recap |
| 2.5 | `sense status`: `state` active\|idle\|offline, heartbeat age, watch health | human-readable + stable keys for agents later |
| 2.6 | Recap precedence: protocol idle > degraded gap; show mode/confidence | away segments tagged `wayland-idle` \| `degraded-gap` |
| 2.7 | Unit systemd: graphical-session + Wayland env docs | documented; idle_watch has display after login |
| 2.8 | Tests: idle_since bias, priority sources, watch death | pytest green |

### Optional in phase 2 (if cheap)

| ID | Deliverable |
|----|-------------|
| 2.9 | Recap: media Playing soft-exception (tag only; not sole NATS activity) |

### Non-goals (phase 2)

- MCP / NATS publish
- cosmic-toplevel (phase later / F)
- Retention prune (unless bloat forces — prefer config stub)

### Verify

```bash
# leave desk 6+ min without lock
sense status   # state=idle, idle_since ≈ last input
sense recap    # away measured, not Chrome-eaten
# kill idle watch process
sense status   # degraded / offline path honest
```

---

## Phase 3 — Surfaces that only read the logger

**Intent:** Agents and factory consume the **same** store; no re-collection.

### Deliverables

| ID | Deliverable | Done when |
|----|-------------|-----------|
| 3.1 | Shared query layer for “active now” / day slice (store or report, not MCP-private) | one function, CLI + MCP call it |
| 3.2 | MCP stdio tools (minimal): `sense_status`, `active_now`, `what_was_i_doing` | tools return redacted-by-default payloads |
| 3.3 | MCP redaction levels: `coarse` default / `standard` / `full` opt-in | no `title_raw` / media track on coarse |
| 3.4 | NATS opt-in: single daemon publisher `activity` \| `stale` | envelope versioned, **no titles**, multi-source hysteresis |
| 3.5 | Config knobs: `mcp.*`, `nats.*`, `collectors.idle_backend`, docs | install + README |
| 3.6 | Tests: no title in NATS fixture; MCP coarse strips titles | pytest green |

### Non-goals (phase 3)

- Sentinelle policy / Discord
- Screenpipe-class signals
- Per-collector NATS publish

### Verify

```bash
# MCP
echo '{"jsonrpc":"2.0",...}' | sense mcp   # or documented client smoke
# NATS off by default
# NATS on: only activity/stale subjects; payload schema check
```

---

## Work order (implementation)

```text
Phase 1  ship recap baseline ─────────────────────────────┐
Phase 2.0 ADR presence/idle ──────────────────────────────┤
Phase 2.1 meta liveness + status fields ──────────────────┤  (can PR-stack)
Phase 2.2–2.4 idle_watch + authority + recap precedence ──┤
Phase 2.5–2.8 status UX + systemd + tests ────────────────┤
Phase 2.9 media soft-exception (optional) ────────────────┤
Phase 3.1 shared queries ─────────────────────────────────┤
Phase 3.2–3.3 MCP ────────────────────────────────────────┤
Phase 3.4–3.6 NATS + config + tests ──────────────────────┘
```

Prefer **PR stack** or sequential PRs per phase; do not merge MCP before 2.x idle/status.

---

## Success metrics

| Metric | Target |
|--------|--------|
| Recap away vs lived absences | No multi-hour “Chrome” blocks when away |
| Status idle without lock screen | Works on Cosmic without LockedHint |
| Store growth | No permanent 2–4 events/min heartbeat |
| Surfaces | MCP/NATS do not reimplement collectors |
| Privacy | NATS never ships window titles by default |

## Failure (abort / reframe)

- Implementing heartbeat firehose “because docs said heartbeat”
- Putting composite idle or SM inside a collector
- MCP/NATS shipping full titles as default
- Scope creep to OCR / a11y text / evdev keylogging

## Panel constraints absorbed

Product / Architect / DevOps / Axial / Adversarial (2026-07-30):

- Heartbeat = coordinator/meta, not peer collector
- SM = shared derived layer
- One idle authority + degraded fallback
- systemd graphical-session + Wayland env
- MCP redaction; NATS coarse multi-source
- Allowlist signals; mark inference confidence

## References

- `docs/PURPOSE.md`
- `docs/architecture/adr/001-axis-of-decomposition.md`
- Session design: Source → Capteur → Logger
- Existing: `src/roxabi_sense/report/`, `collectors/focus_watch.py` pattern
