# roxabi-sense

**Workstation attention journal** — light sensors, local store, agent surfaces.

> Status: **phases 1–3 live** (collectors + store + CLI + daemon + MCP stdio) · NATS still stub  
> Not screenpipe. Not a web SaaS. Not inside `roxabi-factory`.  
> Agent SSOT: [`AGENTS.md`](./AGENTS.md)

---

## Why

Screen capture + OCR is the wrong tool for “what was I doing?”.

You already have timestamped work in `~/.claude` / `~/.grok`, plus live Herdr/OMP panes (tmux still supported). Meetings live in Claap. What is missing is a **cheap focus spine**: which app/window was active, which agent sessions were open, whether Slack/Discord was running — without pixels, keyloggers, or a 174 MB trial for 40 seconds of frames.

`roxabi-sense` is that spine. It publishes **facts**, not policy.

---

## What it is / is not

| Is | Is not |
|---|---|
| Local user-session daemon (systemd `--user`) | Factory hub module |
| CLI + optional MCP + optional NATS publisher | Screen OCR / continuous screenshots |
| Reads Claude/Grok files + live Herdr/OMP (tmux still ok) | Re-logs AI conversations |
| Focus / idle / process presence | Meeting recorder (→ Claap) |
| Edge sensor for Sentinelle later | Sentinelle decision brain (→ factory hub) |

---

## Architecture (target)

```
  collectors (facts only)
  ┌─────────────┐  ┌──────────────┐  ┌────────────────┐
  │ focus/idle  │  │ agent + mux  │  │ process presence│
  │ (Wayland /  │  │ ~/.claude    │  │ slack/discord  │
  │  AT-SPI)    │  │ ~/.grok      │  │ herdr/omp/tmux │
  │             │  │ herdr/OMP    │  │ (running?)     │
  │             │  │ tmux panes   │  │                │
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
sense recap                   # day summary; observed vs inferred meeting evidence
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

Then ask the agent for tools. Heartbeat agents should call **`care_brief`** (not `day_recap`). If tools are missing: restart the host session; if tools return offline/empty: fix daemon/`sense doctor`, not MCP JSON.

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

- Default MCP redaction is **coarse** (no window titles / media tracks / full paths). Full detail only via operator config `[mcp] detail = "full"` — not tool-arg escalation (ADR-002). One bounded exception: `care_brief` carries **agent session titles** (e.g. OMP auto-titles) so the heartbeat can map agent work to priorities — never window titles (ADR-002 §6 amendment).
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
| **2 — focus + idle** | Multi-backend focus probes + idle chain + process/mpris/tmux | **done** (P0+P1) |
| **3 — MCP** | stdio tools over `SenseQuery` (`active_now`, timeline, sessions, …) | **done** |
| **4 — NATS opt-in** | `factory.event.host.{machine}.activity\|stale` for Sentinelle | open |
| **5 — optional** | Filtered browser history, local status HTTP | open |

### Focus probes (multi-Linux)

Focus is one collector (`kind=focus`) with swappable **FocusProbe** backends. The
`source` field identifies the backend (`cosmic` | `atspi` | `x11` | `wlr` | `kde` | `noop`).

| Session | Candidate order (first healthy wins) |
|---------|--------------------------------------|
| Wayland Cosmic / unknown | `cosmic` → `atspi` → `x11` → `noop` |
| Wayland GNOME | `atspi` → `x11` → `noop` |
| Wayland wlroots (Hyprland / Sway) | `wlr` → `atspi` → `x11` → `noop` |
| Wayland KDE | `atspi` → `kde` (stub) → `x11` → `noop` |
| Pure X11 | `x11` → `atspi` → `noop` |

- **COSMIC** — read-only native Wayland toplevel metadata when both COSMIC
  toplevel-info and ext-foreign-toplevel-list are advertised. Capability
  detection does not require `XDG_CURRENT_DESKTOP`; the daemon needs
  `WAYLAND_DISPLAY` and `XDG_RUNTIME_DIR` in its graphical-session environment.
  No screenshots, input hooks, or window-control requests are used.
  `focus_coverage` records transitions; health is stored in `focus_observation`
  metadata, not heartbeat events. Unknown or stale coverage is not counted as
  focus dwell. Historical focus without coverage, including a later fallback
  source marked by `focus_legacy`, is `legacy_unverified`, not certified native
  coverage. Recaps derive current attention from validated focus, continuous
  coverage and the same app/session context used for aggregation: title changes
  do not restart a stretch, but gaps and context changes do.
  A per-database nonblocking ownership lock prevents `collect --once`
  from replacing a running daemon's focus observation; other collectors still run.
- **AT-SPI** — long-lived fallback agent (system Python + `gi`) when accessibility works.
- **X11** — `xprop` / active window (XWayland fallback). Package: **`x11-utils`**.
- **wlr** — `hyprctl activewindow -j` or `swaymsg -t get_tree` when compositor env is set.
- **kde** — stub for now (`probe()` false until KWin D-Bus path lands).
- **Runtime demote** — if AT-SPI dies, daemon switches to next healthy probe without blocking other collectors.
- Meta focus: `focus_backend`, `focus_status`, `session_type`, `desktop_family`, `last_focus_path`.
- Meta idle: `idle_backend`, `idle_status`, `idle_chain_reason` (wayland → logind → noop).

### Meeting evidence

The opt-in Chrome Google Meet connector observes recognized call UI, not human
attention or server-side attendance. It is disabled by default. Coverage is
limited to registered Chrome Google Meet sources, not all meeting apps. Unknown
or unsupported UI and missing, stale or conflicted sources are not proof that
no call occurred. Recognition uses specific English UI labels; other languages,
redesigned or reconnecting layouts can be unknown. Joined and reconnecting
layouts have not been validated in a real call; this is not a real deployment.

`meeting_total_s` unions observed call intervals; title-derived legacy signals
are inferred and reported separately as `meeting_inferred_s`. Union components
are evidence segments, not counts of physical calls: a gap can split one call,
and concurrent calls can merge. Recaps expose coverage, unknown time, quality
and event-window truncation, distinct from JSON output-size truncation. A single
observation has no historical duration; gaps stop confirmed time at the last
observation. Truncated input cannot certify or extend into an unseen suffix.
Focus remains a parallel track, so use `activity_union_s` rather than adding
focus and meeting totals. The connector collects no meeting URLs, titles,
participants, DOM text or audio; opaque source and tab identifiers are excluded
from coarse exports. See [ADR-004](docs/architecture/adr/004-meeting-sessions-parallel-track.md).

#### Install and operate

The native-host registration uses a Linux Chrome/Chromium user-data-directory
layout. Use the actual browser and profile you intend to observe; packaged or
custom browser layouts may differ. Keep the exported extension directory and the
invoking `sense` installation available for the lifetime of the registration.

1. Install `sense` as a stable tool from a durable clone as in [Setup](#setup-path-stable),
   not from a temporary worktree or transient `uv run` environment. The native
   launcher pins the invoking installation, not whichever `sense` is later on PATH.
2. In the config used by the daemon and commands, opt in:

   ```toml
   [collectors]
   meeting_browser = true
   ```

3. Export to a new or empty directory, then open `chrome://extensions`, enable
   **Developer mode**, choose **Load unpacked**, and select that directory.
   Copy the exact **ID** shown on the loaded extension's card. Keep the directory.

   ```bash
   sense meeting-browser export "$HOME/roxabi-sense-meet-extension"
   ```

4. In that browser profile open `chrome://version` and copy **Profile Path**,
   including its profile directory (for example `.../Default`), not a guessed
   default. Use the same config and database as the daemon. Substitute the
   actual absolute paths and extension ID below:

   ```bash
   CONFIG="$HOME/.config/roxabi-sense/config.toml"
   PROFILE="/absolute/path/from/chrome-version/Profile Path"
   EXTENSION_ID="paste-the-id-from-chrome-extensions"
   sense --config "$CONFIG" meeting-browser install --profile "$PROFILE" --extension-id "$EXTENSION_ID"
   sense --config "$CONFIG" doctor
   sense --config "$CONFIG" status
   ```

5. Open a Google Meet page in the selected browser and inspect `doctor`, `status`
   and `recap`. A recognized leave control indicates UI evidence only, not
   attendance or attention; unknown coverage is expected for unsupported states.

Registration is **user-data-directory-wide**: the manifest and launcher live in
`NativeMessagingHosts` under the profile directory's parent. `--profile` locates
that directory but does not authenticate or isolate the profile. Alternatively,
for both install and remove, use `--data-dir "/absolute/user-data-directory"`
instead of `--profile`; these options are mutually exclusive. Multiple profiles
with the same extension ID can start competing hosts for one source. A conflict
stops writers and latches coverage unknown; it does not automatically expire.

To disable collection, set `meeting_browser = false` in the same config; the
host reloads consent before writing. To remove registration, use the same config,
profile path and extension ID:

```bash
sense --config "$CONFIG" meeting-browser remove --profile "$PROFILE" --extension-id "$EXTENSION_ID"
```

Disable or remove the unpacked extension in `chrome://extensions` as well. For
conflict recovery, first stop the duplicate producers, complete owned removal
(retry the same command if cleanup was interrupted), then reinstall with the
same install command and leave only one producer active. Modified or unowned
surviving files are not deleted automatically. Remove registration **before**
moving or uninstalling the pinned executable, its environment or editable clone;
after installing at the new stable location, register again. A config change
does not silently migrate the active store.

### Agent sessions (Grok / Claude / Herdr·OMP / optional Cursor)

| Source | Default | Paths (read-only) |
|--------|---------|-------------------|
| Grok | on (`agent_sessions`) | `~/.grok/active_sessions.json` |
| Claude | on (`agent_sessions`) | `~/.claude/sessions/*.json` |
| Herdr / OMP | on (`herdr`) | live `herdr agent list` + `pane list` (cwd, status, session_id, session title without spinner, focused pane; no raw terminal titles, no jsonl) |
| tmux | on (`tmux`) | `tmux_snapshot` panes (grok/claude/omp still match) |
| Cursor | **off** (`cursor_sessions = true`) | `~/.config/Cursor/User/workspaceStorage/*/workspace.json` |

Cursor opt-in emits `agent_sessions_snapshot` with `agent=cursor` (workspace id + folder path + mtime only). **Never** opens chat DBs (`state.vscdb`), composer history, or rewrites agent dirs.

```toml
# ~/.config/roxabi-sense/config.toml
[collectors]
cursor_sessions = true
# cursor_root = "/home/you/.config/Cursor"   # optional override
# cursor_max_workspaces = 20
# cursor_max_age_days = 30
```

Agent status detail: [`AGENTS.md`](./AGENTS.md) § Status.

Out of scope forever (for this repo): OCR, continuous screenshots, keylogging, clipboard dumps, scraping Slack/Discord desktop clients, meeting transcription, cloud upload of agent chat bodies.

---

## Relation to the rest of Roxabi

| Project | Relation |
|---|---|
| [`roxabi-factory`](https://github.com/Roxabi/roxabi-factory) | Future **consumer** (Sentinelle hub module). Not the home of collectors. ADR-091 `factory-host-sensor` role lives *here* as an edge process. |
| [`roxabi-cortex`](https://github.com/Roxabi/roxabi-cortex) | Downstream memory/insight may *ingest* sense observations later. Sense stays capture + query, not the entity graph. |
| Claap | Meetings — do not duplicate |
| `~/.claude` / `~/.grok` / `~/.omp` | Read-only sources for agent presence (Herdr live list; never rewrite) |

---

## License

AGPL-3.0-or-later (same family as `roxabi-cortex`).

---

## Status

Phases 1–3 live (collectors, store, CLI, daemon, MCP stdio). Host registration: README § MCP host registration. NATS still open.
