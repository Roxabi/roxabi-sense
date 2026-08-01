# roxabi-sense

**Workstation attention journal** — light sensors, local store, agent surfaces.

> Status: **phases 1–3 live** (collectors + store + CLI + daemon + MCP stdio) · NATS still stub  
> Not screenpipe. Not a web SaaS. Not inside `roxabi-factory`.  
> Agent SSOT: [`AGENTS.md`](./AGENTS.md) · Claude shim: [`CLAUDE.md`](./CLAUDE.md)

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

### Install matrix

| Layer | What it does | How |
|-------|----------------|-----|
| **Data plane** | Always-on collectors → SQLite | `sense install-service` + `systemctl --user enable --now roxabi-sense.service` |
| **Query plane** | Read API for humans/agents | CLI: `sense status` / `recap` · MCP: `sense mcp` (stdio) |
| **Agent DX** | Host wires MCP | Grok/Claude host snippets **or** thin plugin `plugins/roxabi-sense/` (`.mcp.json` → PATH `sense mcp`) |

Data plane and query plane are **separate**: MCP does not start collectors. Empty/offline tools ⇒ fix the daemon, not the agent config.

### Setup (PATH-stable)

Prefer a **stable `sense` on PATH** so agent configs never hardcode a worktree path.

```bash
# 1. clone (or pull) + install CLI + MCP deps into uv tool env
git clone git@github.com:Roxabi/roxabi-sense.git
cd roxabi-sense
uv tool install -e '.[mcp]'
# re-run after pull when the package changes:
#   uv tool install -e '.[mcp]' --force
# After PyPI release: uv tool install 'roxabi-sense[mcp]'

# 2. data plane — user systemd unit (not the same process as MCP)
sense install-service
systemctl --user enable --now roxabi-sense.service

# 3. smoke / DoD
sense doctor                  # FAIL if offline / no DB / MCP missing
sense status                  # last_tick should refresh while daemon is up
sense recap
which sense                   # typically ~/.local/bin/sense

# 5. optional NATS (when factory Sentinelle is ready)
#    sense config set nats.enabled true
```

### MCP host registration (Grok + Claude)

**Prereq:** steps 1–3 above — `which sense` resolves, `sense doctor` is green (daemon + DB + MCP SDK). MCP does **not** start collectors; empty tools ⇒ fix data plane first.

**Happy path:** host spawns `sense mcp` from **PATH** — never a worktree absolute path.

#### Grok

User-global TOML (`~/.grok/config.toml`):

```toml
[mcp_servers.roxabi-sense]
command = "sense"
args = ["mcp"]
enabled = true
```

Or CLI (same result; user scope is default):

```bash
grok mcp add roxabi-sense -- sense mcp
grok mcp doctor roxabi-sense   # config + spawn smoke
```

Restart the Grok session (or open a new one) so the server is re-spawned.

#### Claude Code

**CLI** (preferred one-liner):

```bash
# workstation-wide (recommended for a host sensor)
claude mcp add -s user roxabi-sense -- sense mcp

# or project-local (writes/approves .mcp.json in the repo)
claude mcp add -s project roxabi-sense -- sense mcp
```

**Project** `.mcp.json` (equivalent shape):

```json
{
  "mcpServers": {
    "roxabi-sense": {
      "command": "sense",
      "args": ["mcp"]
    }
  }
}
```

Project-scoped servers may show as pending approval until accepted in Claude. User scope avoids per-repo approval for a machine-local sensor.

**Asymmetry (intentional):** Grok stores servers in TOML (`~/.grok/config.toml` or project `.grok/config.toml`); Claude uses CLI scopes / `.mcp.json` JSON. Both spawn the same stdio command: `sense` + `mcp`.

#### After registration

```bash
sense doctor          # still green (host config is not a substitute for data plane)
sense status          # last_tick moving while daemon is up
```

Then ask the agent for tools (`sense_status`, `active_now`, `what_was_i_doing`, …). If tools are missing: restart the host session; if tools return offline/empty: fix daemon/`sense doctor`, not MCP JSON.

#### Hardening (optional)

Pin absolute binary if PATH is unreliable in the agent environment:

```toml
# Grok example — replace with real path from `which sense`
[mcp_servers.roxabi-sense]
command = "/home/YOU/.local/bin/sense"
args = ["mcp"]
enabled = true
```

#### Dev-only fallback (not for agent configs)

```bash
# contributor smoke — do not paste worktree paths into host MCP configs
uv run --extra mcp --directory /path/to/durable/clone sense mcp
```

Editable `uv tool install -e` must point at a **durable clone** (e.g. `~/projects/roxabi-sense`), not a feature worktree. If you delete that worktree, `sense` breaks with `ModuleNotFoundError` — reinstall:

```bash
cd ~/projects/roxabi-sense   # durable path
uv tool install -e '.[mcp]' --force
sense doctor
```

#### Privacy / trust

- Default MCP redaction is **coarse** (no window titles / media tracks / full paths). Full detail only via operator config `[mcp] detail = "full"` — not tool-arg escalation (ADR-002).
- Agent spawn trusts the `sense` binary on PATH. Prefer operator-owned `uv tool` install over a world-writable clone.
- Only wire agents you trust with activity metadata.

### Thin agent plugin (optional)

Optional DX package under [`plugins/roxabi-sense/`](plugins/roxabi-sense/) — **wiring only**:

| Ships | Does not ship |
|-------|----------------|
| `.mcp.json` → `command: sense`, `args: [mcp]` | Python runtime, collectors, AT-SPI |
| Skill: when/how to use `sense_*` tools | Second query layer / private SQL |
| Plugin README + missing-PATH fallback | systemd unit / daemon start |

```bash
# Same happy path as host snippets — plugin just packages it:
#   command = "sense"  args = ["mcp"]
# Manual still wins until marketplace publish:
grok mcp add roxabi-sense -- sense mcp
claude mcp add -s user roxabi-sense -- sense mcp
```

Requires `roxabi-sense[mcp]` on PATH (package ≥ 0.0.1 with MCP extra) and `sense doctor` green. Full plugin notes: [`plugins/roxabi-sense/README.md`](plugins/roxabi-sense/README.md).

Contributor / in-tree workflow (not for host MCP config):

```bash
cd roxabi-sense && uv sync --extra mcp
uv run sense status
```

No Podman required on the laptop for V1. M₂ may use the same user unit. M₁ host-sensor path (services snapshot only) is a later collector, not a Quadlet of this whole app.

---

## Roadmap (coarse)

| Phase | Deliverable | State |
|---|---|---|
| **0 — scaffold** | Public repo, purpose, architecture | **done** |
| **1 — local spine** | Agent-session collector + store + CLI `status` / `day` / `recap` | **done** |
| **2 — focus + idle** | AT-SPI focus + idle (Wayland / logind) + process/mpris/tmux | **done** |
| **3 — MCP** | stdio tools over `SenseQuery` (`active_now`, timeline, sessions, …) | **done** |
| **4 — NATS opt-in** | `factory.event.host.{machine}.activity\|stale` for Sentinelle | open |
| **5 — optional** | Filtered browser history, local status HTTP | open |

Agent status detail: [`AGENTS.md`](./AGENTS.md) § Status.

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

Phases 1–3 live (collectors, store, CLI, daemon, MCP stdio). Host registration: README § MCP host registration. NATS still open.
