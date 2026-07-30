# roxabi-sense

**Workstation attention journal** — light sensors, local store, agent surfaces.

> Status: **scaffold** · design locked · implementation not started  
> Not screenpipe. Not a web SaaS. Not inside `roxabi-factory`.

---

## Why

Screen capture + OCR is the wrong tool for “what was I doing?”.

You already have timestamped work in `~/.claude` and `~/.grok`. Meetings live in Claap. What is missing is a **cheap focus spine**: which app/window was active, which agent sessions were open, whether Slack/Discord was running — without pixels, keyloggers, or a 174 MB trial for 40 seconds of frames.

`roxabi-sense` is that spine. It publishes **facts**, not policy.

---

## What it is / is not

| Is | Is not |
|---|---|
| Local user-session daemon (systemd `--user`) | Factory hub module |
| CLI + optional MCP + optional NATS publisher | Screen OCR / continuous screenshots |
| Reads existing Claude/Grok session artifacts | Re-logs AI conversations |
| Focus / idle / process presence | Meeting recorder (→ Claap) |
| Edge sensor for Sentinelle later | Sentinelle decision brain (→ factory hub) |

---

## Architecture (target)

```
  collectors (facts only)
  ┌─────────────┐  ┌──────────────┐  ┌────────────────┐
  │ focus/idle  │  │ agent sessions│  │ process presence│
  │ (Wayland /  │  │ ~/.claude    │  │ slack/discord  │
  │  AT-SPI)    │  │ ~/.grok      │  │ (running?)     │
  └──────┬──────┘  └──────┬───────┘  └───────┬────────┘
         │                │                   │
         └────────────────┼───────────────────┘
                          ▼
                 local store (SQLite)
                 ~/.local/share/roxabi-sense/
                          │
          ┌───────────────┼────────────────┐
          ▼               ▼                ▼
        CLI             MCP              NATS (opt-in)
     day-slice      agent query    factory.event.host.*
     status         what_was_i…    activity | stale only
```

**Boundary (ADR-091-aligned):** sensors publish facts; `roxabi-factory` Sentinelle (hub module, not shipped yet) may *consume* host events and decide. This repo never opens Discord, never dispatches jobs, never applies ops policy.

| Surface | Granularity | Depends on factory? |
|---|---|---|
| Local store + CLI | Fine timeline | No |
| MCP (stdio) | Query on demand | No |
| NATS plane ① | Coarse heartbeats (`activity` / `stale`) | Yes (bus up) |

Details: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) · purpose: [`docs/PURPOSE.md`](docs/PURPOSE.md)

---

## Stack decision

**Local daemon + CLI + MCP — not a Silex web boilerplate.**

| Candidate | Verdict |
|---|---|
| `silex-boilerplate` / Next / multi-page web app | **No** — that shape is for client demos & product UI, not a user-session sensor |
| Full stack inside `roxabi-factory` | **No** — factory owns Sentinelle *consumer*; capture stays on the workstation |
| **Python 3.13 + `uv` + systemd `--user`** | **Yes** — matches Roxabi satellites (`voiceCLI`, `xcli`, cortex packages) |
| SQLite under `~/.local/share/roxabi-sense/` | **Yes** — single-machine, Syncthing-friendly if paused |
| MCP stdio | **Yes** — Claude / Grok / Hermes |
| NATS publisher (optional extra) | **Yes later** — facts only, feature-flagged |
| Tiny `127.0.0.1` status page | **Optional V2** — human glance only, not the product surface |

### Setup model (target)

```bash
# 1. install
git clone git@github.com:Roxabi/roxabi-sense.git
cd roxabi-sense && uv sync

# 2. enable user service (writes unit under ~/.config/systemd/user/)
uv run sense install-service
systemctl --user enable --now roxabi-sense.service

# 3. human CLI
uv run sense status
uv run sense day --from 14:00 --to 16:00

# 4. agents (Claude Code / Grok) — MCP stdio
#    mcpServers.sense.command = ["uv", "run", "--directory", ".../roxabi-sense", "sense", "mcp"]

# 5. optional NATS (when factory Sentinelle is ready)
#    sense config set nats.enabled true
#    sense config set nats.url nats://...
```

No Podman required on the laptop for V1. M₂ may use the same user unit. M₁ host-sensor path (services snapshot only) is a later collector, not a Quadlet of this whole app.

---

## Roadmap (coarse)

| Phase | Deliverable |
|---|---|
| **0 — this scaffold** | Public repo, purpose, architecture, empty package |
| **1 — local spine** | Agent-session collector + store + CLI `status` / `day` |
| **2 — focus** | Wayland/COSMIC focus + idle (AT-SPI first) |
| **3 — MCP** | 3–5 tools (`active_now`, `what_was_i_doing`, `agent_sessions`, …) |
| **4 — NATS opt-in** | `factory.event.host.{machine}.activity\|stale` for Sentinelle |
| **5 — optional** | Process presence, filtered browser history, local status HTTP |

Out of scope forever (for this repo): OCR, continuous screenshots, keylogging, clipboard dumps, scraping Slack/Discord desktop clients, meeting transcription.

---

## Relation to the rest of Roxabi

| Project | Relation |
|---|---|
| [`roxabi-factory`](https://github.com/Roxabi/roxabi-factory) | Future **consumer** (Sentinelle hub module). Not the home of collectors. ADR-091 `factory-host-sensor` role lives *here* as an edge process. |
| [`roxabi-cortex`](https://github.com/Roxabi/roxabi-cortex) | Downstream memory/insight may *ingest* sense observations later. Sense stays capture + query, not the entity graph. |
| Claap | Meetings — do not duplicate |
| `~/.claude` / `~/.grok` | Read-only sources for agent presence |

---

## License

AGPL-3.0-or-later (same family as `roxabi-cortex`).

---

## Status

Scaffold only — no collectors yet. Design intent is frozen enough to implement phase 1 without another product loop.
