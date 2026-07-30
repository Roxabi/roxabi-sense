# Architecture

Living overview: **[`../ARCHITECTURE.md`](../ARCHITECTURE.md)** · purpose: **[`../PURPOSE.md`](../PURPOSE.md)**.

## High-level

```
collectors → store (SQLite) → surfaces (CLI | MCP | NATS opt-in)
```

Local workstation sensor only. Policy (Sentinelle) stays in factory-hub when wired.

## Axis of decomposition

Canonical: **[`adr/001-axis-of-decomposition.md`](adr/001-axis-of-decomposition.md)** (`axial: true`).

| Axis | Role |
|------|------|
| **Collector / signal source** | **Primary** — one module per fact source |
| Surface (CLI, MCP, NATS) | Secondary adapters over store |
| Host (laptop, M₂) | Config / machine id only |

## Layers

| Path | Responsibility |
|------|----------------|
| `src/roxabi_sense/collectors/` | Typed facts only (no Discord / jobs / policy) |
| `src/roxabi_sense/store/` | SQLite WAL + shared queries |
| `src/roxabi_sense/surfaces/` | Thin CLI / MCP / NATS adapters |
| `deploy/` | systemd --user unit |

## ADRs

| ADR | Title |
|-----|-------|
| [001](adr/001-axis-of-decomposition.md) | Axis of Decomposition |
