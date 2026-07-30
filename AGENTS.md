# AGENTS.md — roxabi-sense

## What this is

Local workstation **attention sensor**: collectors → SQLite → CLI / MCP / optional NATS.

## Hard rules

- **Facts only** in collectors — no Discord, no job dispatch, no Sentinelle policy.
- **No OCR / screenshots / keylogging** as product direction.
- Read `~/.claude` and `~/.grok` **read-only**; never rewrite agent histories.
- NATS payloads stay **coarse** (`activity` / `stale`); no title firehose by default.
- Stack = Python + uv + systemd user — **not** silex-boilerplate / hosted web app.

## Docs

- Purpose → `docs/PURPOSE.md`
- Architecture → `docs/ARCHITECTURE.md`
- Human entry → `README.md`

## Implementation order

1. Agent-session collector + store + `sense status` / `sense day`
2. Idle + focus (behind interface; Cosmic/Wayland may lag)
3. MCP tools
4. NATS opt-in for factory Sentinelle
