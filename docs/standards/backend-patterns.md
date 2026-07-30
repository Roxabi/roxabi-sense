# Backend Patterns — roxabi-sense

Project-specific conventions. Agents read this via `{standards.backend}`.

## Stack

| Choice | Value |
|--------|-------|
| Language | Python ≥ 3.13 |
| PM | uv + hatchling |
| Package | single `roxabi_sense` under `src/` |
| DB | SQLite WAL (`~/.local/share/roxabi-sense/sense.db`) |
| Runtime | systemd --user (`deploy/roxabi-sense.service`) |
| Lint/format | ruff |
| Types | pyright (basic) |
| Tests | pytest |

No FastAPI product surface. No Next/Silex web app. Optional loopback status page later is not the spine.

## Module structure

```
src/roxabi_sense/
  collectors/   # primary axis — one source of signal per module
  store/        # append + query
  surfaces/     # cli / mcp / nats — adapters only
  cli.py        # entrypoints
```

## Rules

1. **Collectors emit facts only** — never Discord, job dispatch, or Sentinelle policy.
2. **Surfaces query the store** — do not reimplement collection or timeline logic per surface.
3. **Read-only agent histories** — `~/.claude`, `~/.grok` are inputs, never rewritten.
4. **NATS stays coarse** — `activity` / `stale` only; no title firehose by default.
5. **Focus/Wayland behind an interface** — failure must not block agent-session collection.

## Quality gates

- File length ≤ 300 lines (`src/**/*.py`) — `tools/check_file_length.sh`
- Folder size ≤ 12 files per dir under `src/**` — `tools/check_folder_size.sh`
- Import layers off until collectors/store/surfaces stabilize

## Config

`~/.config/roxabi-sense/config.toml` (see `docs/ARCHITECTURE.md`). Env override for DB path is fine; secrets never in repo.
