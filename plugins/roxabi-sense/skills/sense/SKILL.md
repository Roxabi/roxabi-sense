---
name: sense
description: >
  Use roxabi-sense MCP tools for local workstation attention facts (presence,
  focus, agent sessions, day timeline/recap). Trigger when the user asks what
  they were doing, whether they are idle, which agent sessions are open, or
  wants a day recap from the sense store. Requires PATH `sense` + running daemon.
---

# roxabi-sense tools

Local **attention sensor** — facts only. This skill is **wiring guidance**; all data comes from MCP tools backed by `SenseQuery` in the `sense` package.

## Hard rules

1. **Never invent timeline SQL** or open `~/.local/share/roxabi-sense/sense.db` yourself.
2. **Never re-read** `~/.claude` / `~/.grok` session files for presence — the daemon already collects that.
3. **Never start collectors**, edit systemd units, or call Discord/jobs from this path.
4. If tools return offline/empty: fix the **data plane** (`sense doctor`), not host JSON.

## Prerequisites (fail closed)

Before relying on tools:

```bash
which sense && sense doctor
```

| Symptom | Action |
|---------|--------|
| `sense: command not found` | Install: `uv tool install -e '.[mcp]'` from a **durable** clone (or PyPI `roxabi-sense[mcp]`). See repo README install matrix. |
| doctor FAIL / offline | `systemctl --user status roxabi-sense.service` · enable unit · check `last_tick` |
| MCP tools missing in host | Restart agent session; confirm MCP entry is `sense` + `mcp` on PATH (plugin `.mcp.json` or host snippets) |
| `ModuleNotFoundError` on `sense` | Editable install pointed at deleted worktree → reinstall `--force` from durable clone |

## Tools (MCP `roxabi-sense`)

| Tool | Use when |
|------|----------|
| `sense_status` | Daemon health, presence, last collect meta |
| `active_now` | “What am I doing right now?” — presence + focus + open sessions |
| `what_was_i_doing` | Day timeline summaries (`day` = `YYYY-MM-DD`, optional `limit`) |
| `agent_sessions` | Claude/Grok sessions for a day |
| `day_recap` | Compiled recap JSON (apps, away, meetings, agents) |

Default redaction is **coarse** (no window titles / media / full paths). Full detail is operator config only (`[mcp] detail = "full"`) — do not ask tools to escalate.

## Mental model

```
collectors (daemon) → SQLite → SenseQuery → sense mcp (stdio)
                              ↑
                     this plugin only wires the last hop
```

MCP does **not** start the daemon. Empty tools ≠ “plugin broken”.

## Privacy

Only use these tools when the user (or a trusted workflow) wants activity metadata. Do not dump timelines into untrusted contexts.
