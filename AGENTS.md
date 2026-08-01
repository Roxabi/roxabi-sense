# AGENTS.md — roxabi-sense

Let:
  P := this file (product + agent SSOT) | C := CLAUDE.md (thin shim → `@AGENTS.md` + `@.claude/stack.yml`)

Content for agents lives **here**. `CLAUDE.md` is only imports (same pattern as roxabi-factory, metalyde, spark).

## Project

**roxabi-sense** — local workstation **attention sensor**: collectors → SQLite → CLI / MCP / optional NATS.

| | |
|--|--|
| **Repo** | `Roxabi/roxabi-sense` (public AGPL) |
| **Not** | factory-hub · silex-boilerplate · hosted web · screenpipe / OCR / keylogging |
| **Policy** | stays in factory Sentinelle when wired; sensor emits **facts only** |
| **Purpose** | `docs/PURPOSE.md` |
| **Architecture** | `docs/ARCHITECTURE.md` |
| **Axial ADR** | `docs/architecture/adr/001-axis-of-decomposition.md` (`axial: true`) |
| **Stack** | `.claude/stack.yml` · Python ≥3.13 · uv · ruff · pytest · pyright · systemd `--user` |
| **Human entry** | `README.md` |

## TL;DR

- **Primary axis first** (before non-trivial edit): signal **source / collector** — not surface, not host.
- Entry: `/dev #N` → tier (S / F-lite / F-full) → lifecycle
- All code → **worktree** off `main` (`git worktree add ../roxabi-sense-XXX -b feat/XXX-slug main`)
- ¬`--force` | ¬`--hard` | ¬`--amend` | never push without request
- Release: **`main` only** (no staging train) · tags `roxabi-sense/vX.Y.Z` via Release Please

## Core (non-negotiable)

**PRIMARY AXIS:** signal source / collector  
→ `docs/architecture/adr/001-axis-of-decomposition.md`

| Extend by… | Do |
|------------|-----|
| New **collector** | one module under `collectors/` → typed facts into store |
| New **surface** | thin adapter over **store / report** only — ¬ reimplement collection or day logic |
| New **host** | config + machine id — same binary, no host-named package fork |

**Anti-patterns (wrong axis):**
- Query / timeline / `status` / `active_now` logic living under `surfaces/` or copied into MCP/NATS formatters
- Collectors importing `surfaces` or CLI
- Per-host forks (`laptop/` vs `m2/` packages)

Canonical greps:
- `from roxabi_sense.surfaces` inside `collectors/`
- `def (status|day|active_now|what_was_i_doing)` under `surfaces/` with business logic (not just format)

## Hard rules

1. **Facts only** in collectors — no Discord dispatch, no jobs, no Sentinelle policy.
2. **No OCR / screenshots / keylogging** as product direction.
3. Read `~/.claude` and `~/.grok` **read-only** — never rewrite agent histories.
4. **NATS payloads stay coarse** (`activity` / `stale`) — no title firehose by default.
5. **Surfaces query store/report** — do not reimplement collection or timeline per surface.
6. **Focus / Wayland behind an interface** — failure must not block agent-session collection.
7. Do **not** import factory monorepo sources for collectors; cross-link via events + contracts later.

## Status (honest)

| Area | State |
|------|--------|
| Store (SQLite WAL) + CLI `status` / `day` / `recap` | **done** |
| Collectors: agent sessions, idle, focus (AT-SPI), process, mpris, tmux | **done** |
| Daemon + systemd `--user` install | **done** |
| Report layer (presence, day recap, meeting annotate) | **done** |
| MCP surface | **done** stdio (`sense mcp` · `uv sync --extra mcp`) |
| Query API | **done** (`query.SenseQuery` — JSON for MCP / future HTTP / CF) |
| NATS opt-in | **not wired** (optional dep empty) |
| Shared status / event summary | **done** (`report/status.py`, `report/event_summary.py`) |
| CLI surface | **done** (`surfaces/cli.py`; root `cli.py` re-exports) |

**Next product slices:** NATS coarse publish; optional HTTP over `SenseQuery`; Cloudflare port of query contracts when data plane moves.

## Stack & commands

```bash
uv sync
uv run sense --help
uv run sense once          # single collect tick
uv run sense daemon        # foreground collectors
uv run pytest
uv run ruff check
uv run pyright
```

DB default: `~/.local/share/roxabi-sense/sense.db` (override `SENSE_DB`)  
Config: `~/.config/roxabi-sense/config.toml`

## Layout (query — do not invent)

```
src/roxabi_sense/
  collectors/   # primary axis — one signal source per module
  store/        # append + query (SSOT facts)
  report/       # status_snapshot, summarize_event, day recap, presence
  atspi/        # focus probe worker (system Python + gi)
  util/         # pure helpers (time, titles, proc, session registry)
  query.py      # transport-agnostic JSON read API (MCP/HTTP/CF)
  surfaces/     # CLI · MCP stdio · (NATS later) — adapters only
  cli.py        # re-export surfaces.cli:main (script entry)
  daemon*.py    # orchestration
deploy/         # systemd user unit
```

## Dev process (harness)

| Tier | When | Path |
|------|------|------|
| **S** | ≤3 files, no arch risk | implement → pr → review → ship |
| **F-lite** | clear scope, one domain | frame → spec → plan → implement → verify → ship |
| **F-full** | new arch / multi-domain | + analyze |

- Orchestrator: only minor one-liners; else delegate BE → `backend-dev` · tests → `tester` · docs → `doc-writer` · infra → `devops` · fixes → `fixer`
- **No product frontend** (`stack.yml` `frontend: none`) — never spawn `frontend-dev` for this repo
- Before code: read `docs/standards/backend-patterns.md` and/or `testing.md` when non-empty; arch → `docs/architecture/` + axial ADR
- Artifacts: `artifacts/{frames,analyses,specs,plans,visuals}/`
- Git: Conventional Commits `<type>(<scope>): <desc>` · never force/hard/amend
- Machine global `core.hooksPath` → **never** `lefthook install` (kills shared ccc reindex)

## Key files (tools, not preload)

| Path | Role |
|------|------|
| `docs/PURPOSE.md` | Why / non-goals |
| `docs/ARCHITECTURE.md` | Target shape + ecosystem |
| `docs/architecture/adr/001-…` | Axis SSOT |
| `docs/architecture/adr/002-presence-and-idle.md` | Idle authority |
| `docs/standards/backend-patterns.md` | Collectors / store / surfaces rules |
| `docs/standards/frontend-patterns.md` | Explicit “no product FE” |
| `.claude/stack.yml` | Commands, QG, release component |
| `artifacts/analyses/quality-audit/` | Last multi-agent quality audit (local) |

## Anti-patterns (short)

- MCP/CLI each owning private SQL or collector ticks
- Second time/format helper after `util/time.py`
- Host-coupled tests without `tmp_path` / monkeypatch
- Expanding AT-SPI worker into the uv package (keep process isolation)
- README/docs claiming “scaffold / not started” after collectors ship

## Agent workflow

1. Read this file; for arch touch ADR-001 + `ARCHITECTURE.md`
2. Worktree + branch; implement on primary axis when adding capability
3. Verify: `uv run pytest` · `uv run ruff check` · `uv run pyright`
4. Commit / push / PR **on request only**
