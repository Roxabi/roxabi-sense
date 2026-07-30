# Frontend Patterns — roxabi-sense

**No product frontend.** Declared in `.claude/stack.yml`:

```yaml
frontend:
  framework: none
```

## What this means

| Surface | Role |
|---------|------|
| CLI (`sense`) | Human operator |
| MCP stdio | Coding agents |
| NATS (opt-in) | Factory Sentinelle plane |
| Loopback status page (maybe later) | Optional binary feature on `127.0.0.1` only |

Not in scope: Next.js, Silex boilerplate, Cloudflare Pages, multi-tenant web app, shadcn product shell.

## Why

Sense needs the **user graphical session** (focus/AT-SPI) and a **background** collector. A hosted SPA cannot own that. Cross-link factory via coarse events, not a shared web UI.

## Agent guidance

If a task asks for “UI” or “frontend”:

1. Re-read `docs/ARCHITECTURE.md` (Rejected: silex-boilerplate)
2. Prefer CLI table output or MCP tool schemas
3. Escalate only if a loopback dashboard is explicitly requested as an optional surface adapter (not a new product spine)
