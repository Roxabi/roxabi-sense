---
title: "Goal — Presence, idle, surfaces (phases 1→2→3)"
status: active
date: 2026-07-30
updated: 2026-07-30
repo: roxabi-sense
axis: collectors (ADR-001)
review: "Approve with comments — panel 2026-07-30; goal patched"
---

# Goal

Ship an honest **attention presence** pipeline on Cosmic/Linux:

**Source → Capteur → Logger → Vue**, without becoming screenpipe.

## Objective (done when)

An operator can answer, locally:

1. **What was I doing today?** — compiled day recap (focus, away, agent sessions if in DB)
2. **Am I active / idle / offline right now?** — status with **authority + confidence** (not a bare enum)
3. **Can an agent / factory read the same truth thinly?** — MCP local (redacted), NATS coarse later

…using **facts only** in collectors, **one SQLite logger**, **thin surfaces**.

---

## Target pipeline (not phase backlog)

Existing collectors already write process/mpris/agents/focus. **This goal does not re-scope polishing them** except as readers for recap/status.

| Source | Capteur | Logger | Phase of *this* goal |
|--------|---------|--------|----------------------|
| clavier/souris | `idle_watch` (Wayland) | `idle` transitions | **2b** |
| logind IdleHint | `idle` collector (existing) | `idle` (demoted) | **2b** demote |
| fenêtre active | focus AT-SPI (existing) | `focus` | **1** read / **2** dwell |
| Spotify/… | mpris (existing) | `media_snapshot` | **2b** optional tag only |
| ~/.grok / claude | agent_sessions (existing) | snapshots | **1** recap section if present |
| process names | process_presence (existing) | snapshots | read-only status annotation |
| daemon vivant | **coordinator meta** (not peer collector) | `meta.last_tick` | **2a** |

```
SOURCE → CAPTEUR (collectors/) → LOGGER (store) → VUE (report/ + thin CLI/MCP/NATS)
```

Cross-collector SM / liveness = **daemon meta + pure `derive_presence()` in report/store** — never inside a collector, never CLI-only private copy.

---

## Hard rules

- Collectors = **faits purs** (no Discord, jobs, Sentinelle policy)
- No OCR / screenshots / keylogging / a11y tree text / evdev keylogging
- Focus payload **allowlist**: `app`, `title` (optional), `pid`, `source`, `agent` link — no tree walk
- Heartbeat **not** a firehose in `events` (meta liveness only, or transition-only `presence` if ever needed)
- State machine `active|idle|offline` = **one pure function** `derive_presence(...)` in `report/` or `store/`; CLI/MCP/NATS only format
- Media / process = **annotations**, never sole drivers of `active` for SM or NATS
- NATS = `activity` / `stale` only; payload must include `sources[]`, `confidence`, `degraded` — **not** an operator-availability SLA
- MCP/CLI JSON default = **coarse** redaction; `full` = operator config file only (tools ignore client arg escalation)
- Idle **write** authority runtime: **one writer** — Wayland if watch healthy, else logind if enabled, else none (recap uses degraded gap)
- Idle **read** precedence for recap: protocol idle events > degraded gap compile
- Single config threshold: `collectors.idle_threshold_s` default **300** (watch + degraded gap)

---

## Operator state contract

| State | Dominant signal | Meaning |
|-------|-----------------|--------|
| `active` | input not idle (or no idle fact yet + recent focus *with* healthy watch not claiming idle) | Likely human input / attention signals |
| `idle` | input idle (Wayland) or degraded gap when no protocol | No input for ≥ threshold; `idle_since` = last activity evidence |
| `offline` | daemon liveness stale (`last_tick` age > offline threshold) | Sensor not running / not updating — **not** the same as user AFK |
| *(flag)* `degraded` | watch dead, no graphical session, logind-only, gap fallback | Always pair with `authority` + `confidence` — not a 4th enum value if possible |

**Status payload (stable keys for agents)** — always include:

```text
state, authority, confidence, degraded,
last_tick_age_s, idle_watch (ready|dead|restarting|n/a),
idle_since?, threshold_s, session_bound (bool)
```

Examples verify: leave desk 6+ min · kill daemon · kill watch only · no `WAYLAND_DISPLAY` (session_bound=false).

---

## Phase 1 — Logger + baseline recap (foundation)

**Intent:** Land day compiler + degraded away; freeze mental model.

| ID | Deliverable | Done when |
|----|-------------|-----------|
| 1.1 | `report/` day recap | `sense recap` on live DB |
| 1.2 | CLI `sense recap [--date] [--json]` | help + tests |
| 1.3 | Degraded away ≥ 5 min (`focus`+`desktop_snapshot`); start = last activity | Away section; dwell cut |
| 1.4 | Tests recap + gap | pytest green |
| 1.5 | Ship baseline (commit/PR) | CI green; `uv tool install -e .` |
| 1.6 | Recap shows agent sessions section when snapshots exist | visible in recap output |

### Non-goals (phase 1)

- Wayland idle, MCP/NATS, heartbeat rows

### Verify

```bash
uv run pytest -q
sense recap | head -50   # Away + Focus + sessions if any
```

**Note:** Phase 1 code landed in `dcbfdc6` (main local); still need push/PR + reinstall as needed.

---

## Phase 2a — Liveness + status skeleton (demoable without Wayland idle)

**Intent:** Honest daemon offline vs “unknown presence”; shared derive function early (anti axis-trap).

| ID | Deliverable | Done when |
|----|-------------|-----------|
| 2.0 | **ADR-002 presence/idle** accepted | file under `docs/architecture/adr/` |
| 2.1 | Meta liveness only (`last_tick` / presence meta fields); **no** 15–30s event spam | status shows age; no `kind=heartbeat` rows |
| 2.1b | `derive_presence()` in `report/` or `store/`; CLI formats only | single function; unit tests |
| 2.5a | `sense status`: state + authority + confidence + last_tick_age + keys above (may be degraded until 2b) | human + stable keys |
| 2.7a | `sense install-service` real (unit path, daemon-reload) | not stub exit 2 |
| 2.7b | Unit: `WantedBy=graphical-session.target` + Wayland env docs | login → env present |
| 2.8a | Tests: offline when last_tick stale | pytest |

### Non-goals (2a)

- idle_watch Wayland
- MCP/NATS

### Verify

```bash
sense status          # last_tick_age_s, state offline if daemon stopped
systemctl --user stop roxabi-sense.service && sense status   # offline
```

---

## Phase 2b — Capteur idle Wayland + recap precedence

**Intent:** Real input idle on Cosmic without lock.

| ID | Deliverable | Done when |
|----|-------------|-----------|
| 2.2 | `idle_watch` subprocess (`ext-idle-notify`, prefer `get_input_idle_notification`, threshold from config) | transitions only; `source`, `idle_since=last_activity` (not event_ts alone) |
| 2.3 | Respawn watch (backoff); crash ≠ kill agents; meta `idle_watch=dead` | kill watch → degrade; restart recovers |
| 2.4 | **Write policy:** Wayland writer when healthy; logind demoted/off | one authoritative writer; recap not dual-conflicting |
| 2.6 | Recap: protocol idle > degraded gap; tags `wayland-idle` \| `degraded-gap` + mode/confidence | away not Chrome-eaten when protocol works |
| 2.8b | Tests: idle_since bias, priority sources, watch death | pytest |
| 2.9 | Optional: media Playing as **annotation only** (never sole `active`/NATS activity) | tagged in recap if done |

### Non-goals (2b)

- MCP/NATS, cosmic-toplevel, retention prune (config stub OK)

### Verify

```bash
# leave desk 6+ min without lock
sense status   # idle, idle_since ≈ last input, authority=wayland-idle
sense recap    # away measured
# kill idle watch
sense status   # degraded / not confident active
```

---

## Phase 3a — MCP (local agents; factory down OK)

| ID | Deliverable | Done when |
|----|-------------|-----------|
| 3.1 | Shared queries already used by CLI (extend if needed) | MCP calls **same** `derive_presence` / day slice |
| 3.2 | MCP tools: `sense_status`, `active_now`, `what_was_i_doing` | redacted-by-default |
| 3.3 | Redaction: coarse default / standard / full via **config file only** | client cannot escalate to full via tool args |
| 3.6a | Tests: coarse strips titles / title_raw / media tracks | pytest |

### Non-goals (3a)

- NATS, Sentinelle, Discord

---

## Phase 3b — NATS opt-in (factory)

| ID | Deliverable | Done when |
|----|-------------|-----------|
| 3.4 | Single daemon publisher `activity` \| `stale` | envelope versioned; **no titles**; `sources[]`, `confidence`, `degraded` required; multi-source hysteresis; media-alone ≠ activity |
| 3.5 | Config `nats.*`, `machine` id; docs | off by default |
| 3.6b | Tests: no title in NATS fixture; media-only not activity | pytest |

### Non-goals (3b)

- Per-collector NATS, policy Discord/jobs

### Verify

```bash
# NATS off by default
# NATS on: subjects host.{machine}.activity|stale only; schema check
```

---

## Work order

```text
Phase 1   ship recap baseline (done locally dcbfdc6) ──┐
Phase 2.0 ADR-002 accepted ────────────────────────────┤
Phase 2a  meta + derive_presence + status + install ───┤
Phase 2b  idle_watch + write authority + recap tags ──┤
Phase 3a  MCP redacted ────────────────────────────────┤
Phase 3b  NATS coarse ─────────────────────────────────┘
```

Do **not** merge MCP before 2b idle/status honest.  
Do **not** implement SM only in `cli.py` (extract for 2.5a).

---

## Success metrics

| Metric | Target |
|--------|--------|
| Recap away vs absences | No multi-hour “Chrome” when away + protocol idle works |
| Status idle without lock | Cosmic without LockedHint |
| Store growth | No permanent 2–4 events/min heartbeat; idle = transitions only |
| Surfaces | MCP/NATS do not reimplement collectors |
| Privacy NATS | Never ships window titles |
| Privacy local | Coarse default CLI/MCP; no title_raw/media/cwd on coarse |
| Honesty | Status/NATS always carry confidence/degraded/sources where applicable |

## Failure (abort / reframe)

- Heartbeat firehose in `events` “because docs said so”
- SM or composite idle inside a collector
- MCP/NATS full titles by default
- Scope creep OCR / a11y text content / evdev
- Dual-write idle without authority filter

## Panel constraints (absorbed 2026-07-30)

- Heartbeat = coordinator/meta  
- SM = shared pure derive  
- One idle **write** authority + degraded read fallback  
- systemd graphical-session + Wayland env  
- MCP redaction config-bound; NATS coarse multi-source + confidence  
- Allowlist focus fields; mark inference confidence  
- install-service real; split 2a/2b and 3a/3b  

## References

- `docs/PURPOSE.md`
- `docs/architecture/adr/001-axis-of-decomposition.md`
- `docs/architecture/adr/002-presence-and-idle.md` (phase 2.0)
- `src/roxabi_sense/report/`, `collectors/focus_watch.py`
