@.claude/stack.yml

# CLAUDE.md — Instructions for Claude Code (roxabi-sense)

## Project

**roxabi-sense** — local workstation **attention sensor**: collectors → SQLite → CLI / MCP / optional NATS.

Not factory-hub. Not silex-boilerplate / hosted web. Sensor facts only; policy stays in factory Sentinelle when wired.

→ `docs/PURPOSE.md` · `docs/ARCHITECTURE.md` · `docs/architecture/adr/` · `AGENTS.md`

## TL;DR

- **Project:** roxabi-sense · repo `Roxabi/roxabi-sense` (public AGPL)
- **Stack:** Python ≥3.13 · uv · ruff · pytest · pyright · systemd --user
- **Primary axis:** signal source / collector (see axial ADR) — surfaces (CLI/MCP/NATS) are secondary adapters
- **All code changes** → worktree: `git worktree add ../roxabi-sense-XXX -b feat/XXX-slug main`
- **Before code:** Read relevant standards doc (see Coding Standards)
- **Orchestrator** delegates to agents — only minor fixes directly
- **Never** OCR / screenshots / keylogging as product direction
- **Never** rewrite `~/.claude` / `~/.grok` histories (read-only collectors)

## Critical Rules

### 1. Dev Process

**Entry point: `/dev #N`** — single command that scans artifacts, shows progress, and delegates to the right phase skill.

| Tier | Criteria | Phases |
|------|----------|--------|
| **S** | ≤3 files, no arch, no risk | triage → implement → pr → validate → review → fix* → cleanup* |
| **F-lite** | Clear scope, single domain | Frame → spec → plan → implement → verify → ship |
| **F-full** | New arch, unclear reqs, >2 domains | Frame → analyze → spec → plan → implement → verify → ship |

`*` = conditional (runs only if applicable)

Phases: **Frame** (problem) → **Shape** (spec) → **Build** (code) → **Verify** (review) → **Ship** (release).

### 2. Orchestrator Delegation

Orchestrator does not modify code/docs directly. Delegate: FE→`frontend-dev` | BE→`backend-dev` | Infra→`devops` | Docs→`doc-writer` | Tests→`tester` | Fixes→`fixer`. Exception: typo/single-line. Deploy→`devops` only.

### 3. Parallel Execution

≥3 complex tasks → propose Sequential | Parallel (Recommended).
F-full + ≥4 independent tasks in 1 domain → multiple same-type agents on separate file groups.

### 4. Git

Format: `<type>(<scope>): <desc>`
Types: feat|fix|refactor|docs|style|test|chore|ci|perf
Never push without request. Never force/hard/amend. Hook fail → fix + NEW commit.

Release: **trunk on `main`** — tags `roxabi-sense/vX.Y.Z` via Release Please. No staging-train.

### 5. Artifact Model

Artifacts are the state markers `/dev` uses for progress detection and resumption.

| Type | Directory | Question answered |
|------|-----------|-------------------|
| **Frame** | `artifacts/frames/` | What's the problem? |
| **Analysis** | `artifacts/analyses/` | How deep is it? |
| **Spec** | `artifacts/specs/` | What will we build? |
| **Plan** | `artifacts/plans/` | How do we build it? |
| **Visuals** | `artifacts/visuals/` | Architecture diagrams (forge-chart sidecars) |

### 6. Mandatory Worktree

```bash
git worktree add ../roxabi-sense-XXX -b feat/XXX-slug main
cd ../roxabi-sense-XXX && uv sync
```

Worktree **mandatory** for all tiers (XS, S, F-lite, F-full) — no exceptions. Only skipped for `/dev` pre-implementation artifacts (frame, analysis, spec, plan) and `/promote` release artifacts.
**Never code on main without worktree.**

### 7. Code Review

MUST read [code-review](docs/standards/code-review.md). Conventional Comments. Block only: security, correctness, standard violations.

### 8. Coding Standards

| Context | Read |
|---------|------|
| Collectors / store / CLI | [backend-patterns](docs/standards/backend-patterns.md) |
| Tests | [testing](docs/standards/testing.md) |
| Architecture / axis | [docs/architecture/](docs/architecture/) + axial ADR |

### 9. Sense hard rules

- **Facts only** in collectors — no Discord, no job dispatch, no Sentinelle policy
- Collectors read agent state **read-only**
- NATS payloads stay **coarse** (`activity` / `stale`); no title firehose by default
- Implementation order: agent_sessions + store + CLI → idle/focus → MCP → NATS

## Skills & Agents

Skills: always use appropriate skill. Workflow skills → `dev-core` plugin.
Agents: Sonnet = all agents (frontend-dev, backend-dev, devops, doc-writer, fixer, tester, architect, product-lead, security-auditor).

**Shared agent rules:** Never force/hard/amend | Stage specific files only | Escalate blockers → lead | Message lead on completion.

## Gotchas

- Machine uses global `core.hooksPath` → **never** `lefthook install` / local hooksPath pirate (kills shared post-merge ccc reindex)
- Focus/Wayland is phase 2 behind an interface — do not block agent-session collector
- Do not import factory monorepo sources for collectors; cross-link via events + contracts later
