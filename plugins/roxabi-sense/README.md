# roxabi-sense (thin agent plugin)

**Wiring only.** This plugin registers the local MCP server as `sense mcp` on **PATH**. It does **not** ship Python, collectors, AT-SPI, or systemd.

| Layer | Who owns it |
|-------|-------------|
| Data plane (daemon → SQLite) | `roxabi-sense` package + user unit |
| Query plane (`SenseQuery` / `sense mcp`) | same package (`[mcp]` extra) |
| This plugin | host MCP config + optional skill |

## Prerequisites

```bash
# durable clone or PyPI — never a feature worktree for editable install
uv tool install -e '.[mcp]'   # from clone; --force after upgrades
# After release: uv tool install 'roxabi-sense[mcp]'

sense install-service
systemctl --user enable --now roxabi-sense.service
sense doctor                  # must be green before relying on tools
```

Requires a `sense` binary that exposes `sense mcp` (package **≥ 0.0.1** with MCP extra). Version source of truth: repo root `pyproject.toml`.

## Install plugin

### Claude Code

From a clone of this repo (or any checkout that includes `plugins/roxabi-sense`):

```bash
# marketplace-style local path (Claude Code)
claude plugin marketplace add /path/to/roxabi-sense   # if/when listed
# or enable the plugin directory directly when your host supports it:
#   claude plugin install --path plugins/roxabi-sense

# Equivalent manual MCP (no plugin):
claude mcp add -s user roxabi-sense -- sense mcp
```

Until marketplace publish (intentionally deferred), prefer **manual host snippets** in the [root README](../../README.md#mcp-host-registration-grok--claude) or point Claude/Grok at this folder’s `.mcp.json`.

### Grok

```bash
# Manual (recommended until marketplace listing):
grok mcp add roxabi-sense -- sense mcp

# Or install this directory as a local plugin if your Grok build supports --plugin-dir:
#   grok agent --plugin-dir /path/to/roxabi-sense/plugins/roxabi-sense …
```

Plugin `.mcp.json` is identical to the happy-path host snippet: `command = "sense"`, `args = ["mcp"]`.

## If `sense` is missing from PATH

1. `which sense` — should resolve (often `~/.local/bin/sense`)
2. `sense doctor` — FAIL on binary/mcp_sdk → reinstall from a **durable** clone:
   ```bash
   cd ~/projects/roxabi-sense
   uv tool install -e '.[mcp]' --force
   ```
3. Do **not** paste a feature-worktree path into MCP config
4. Full matrix: [root README](../../README.md#setup-path-stable)

## Hard rules (plugin authors / skills)

- **No** second query implementation (no private SQL, no re-read of `~/.claude` / `~/.grok` from skills)
- **No** collectors, daemon start, or policy (Discord / jobs / Sentinelle) in the plugin
- Tools are **facts only**; default MCP redaction is coarse

## Skill

`skills/sense/` — when to call `sense_*` tools, daemon vs query plane, missing-binary fallback.
