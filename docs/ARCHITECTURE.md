# Architecture — roxabi-sense

> Living overview. Implementation may lag; this doc is the target shape.

## Placement in the ecosystem

```
 Workstation (laptop / M₂)              Factory (M₁)
 ┌──────────────────────────┐           ┌─────────────────────────┐
 │ roxabi-sense             │  NATS     │ factory-hub             │
 │  collectors → store      │ ──opt──►  │  Sentinelle module      │
 │  CLI · MCP               │  host.*   │  (subscribe, decide)    │
 └──────────────────────────┘           └───────────┬─────────────┘
        ▲                                           │
        │ read                                      ▼
 ~/.claude  ~/.grok                         factory-discord
   (never rewrite)                          #sentinelle (future)
```

**ADR-091 mapping:** this binary is the former `factory-host-sensor` / `sentinel-collect` *role* — sensor only, no policy. Policy stays in the hub when Sentinelle ships.

## Components

| Component | Responsibility | Process model |
|---|---|---|
| **collectors** | Emit typed events (focus, session snapshot, process presence) | Threads / async tasks inside daemon |
| **store** | Append + query SQLite (WAL) | Daemon + CLI readers |
| **cli** | `status`, `day`, `install-service`, `config` | Foreground |
| **mcp** | stdio MCP tools over the same store | Spawned by host agent |
| **nats** (opt) | Publish coarse `factory.event.host.{machine}.{kind}` | Daemon task if enabled |

No multi-tenant web app. No Podman requirement for V1 laptop path.

## Event model (local)

Minimal shapes (illustrative):

```json
{"ts":"2026-07-30T10:05:12Z","kind":"focus","app":"google-chrome","title":"…","pid":1234,"source":"atspi"}
{"ts":"2026-07-30T10:05:12Z","kind":"agent_session","agent":"grok","session_id":"…","cwd":"/home/…","state":"open"}
{"ts":"2026-07-30T10:05:12Z","kind":"process","name":"slack","running":true}
{"ts":"2026-07-30T10:10:00Z","kind":"idle","idle":true,"source":"wayland-idle","threshold_s":300,"idle_since":"2026-07-30T10:05:00Z"}
```

Daemon **liveness** is `meta.last_tick` (and related meta keys), **not** a periodic `kind=heartbeat` event firehose — see ADR-002.

Store path: `~/.local/share/roxabi-sense/sense.db` (override via env / config).

## NATS (optional, phase 4)

| Subject (target) | When | Payload intent |
|---|---|---|
| `factory.event.host.{machine}.activity` | Transition / hysteresis to non-idle presence | Coarse “recent input/attention signals” + `sources[]` / `confidence` / `degraded` |
| `factory.event.host.{machine}.stale` | Quiet / offline / degraded idle | Coarse “no recent human input signal” — not an ops SLA |

Rules:

- Envelope compatible with factory `LyraEvent` / `roxabi-contracts` when wired  
- **No** default stream of every window title to NATS  
- Payload must not rely on media-alone or process-alone for confident `activity`  
- Sensor never calls Discord or dispatches `factory.jobs.*`  
- Presence mapping uses shared `derive_presence` (ADR-002), not per-surface reimplementation

## MCP tools (target V1 surface)

| Tool | Returns |
|---|---|
| `active_now` | Current focus + open agent sessions |
| `what_was_i_doing` | Timeline slice (`from` / `to`) |
| `agent_sessions` | Claude/Grok sessions in window |
| `sense_status` | Daemon health, last collect times |

## Collectors priority

1. **Agent sessions** — parse `~/.grok/active_sessions.json`, session dirs, `~/.claude/history.jsonl` / project JSONL mtimes (read-only). Highest ROI, zero OS integration pain.  
2. **Idle** — Wayland `ext-idle-notify` (primary on Cosmic); logind secondary; see ADR-002.  
3. **Focus** — AT-SPI or Cosmic/Wayland path (hardest; isolate behind interface).  
4. **Process presence** — `pgrep`-class checks for configured app names.  

Focus failure must not block agent-session collection.

## Stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python ≥ 3.13 | Roxabi default; uv workspace-ready |
| Packaging | `uv` + hatchling, single package `roxabi-sense` | Same as small satellites |
| Runtime | `systemd --user` unit | Laptop/M₂ session lifecycle |
| DB | SQLite WAL | Local-first, zero ops |
| MCP | stdio server (sdk TBD at implement) | Agent-native |
| NATS | optional extra + contracts later | Sentinelle plane ① |
| HTTP UI | not V1 | Avoid fake product surface |

### Rejected: silex-boilerplate / full web app

Silex boilerplate targets **deployed multi-role client demos** (static/SSR product shells). Sense needs:

- access to the **user graphical session** (focus, AT-SPI)  
- always-on **background** collection  
- **stdio MCP** for coding agents  
- optional **NATS** to factory  

A Next/CF Pages app cannot own focus collection. A future **loopback status page** (read-only dashboard on `127.0.0.1`) is fine as an optional binary feature; it is not the architecture spine and must not become a hosted multi-tenant product in this repo.

### Rejected: monorepo inside factory

Factory default branch/train, Quadlet fleet, and hub code review load are the wrong velocity for a workstation sensor. Cross-link via **events + contracts**, not via shared `src/factory` imports for collectors.

## Config (target)

`~/.config/roxabi-sense/config.toml`:

```toml
[daemon]
poll_seconds = 5

[collectors]
agent_sessions = true
focus = true
process_presence = true
process_names = ["slack", "discord"]

[nats]
enabled = false
url = ""
machine = "laptop"   # laptop | roxabitower | …
```

## Security / privacy

- Default: data stays on the machine  
- NATS payloads are summaries, not raw UI trees  
- No keylogging; keyboard only via OS focus metadata if ever needed (prefer not)  
- Do not scrape Slack/Discord IndexedDB; use their APIs elsewhere if message content is required  

## Implementation order

See README roadmap. Phase 1 = agent session collector + store + CLI only — proves the loop before Wayland focus.
