"""MCP stdio surface — tools over SenseQuery (transport adapter only).

Run: `sense mcp` (requires optional extra: `uv sync --extra mcp`).

Future:
- same tools via `MCPServer.run(transport=\"streamable-http\")` for remote MCP
- HTTP REST can call `SenseQuery` methods directly (same JSON shapes)
- Cloudflare Workers reimplement `SenseQuery` contracts against D1/R2
"""

from __future__ import annotations

import json
import sys
from typing import Any

from roxabi_sense.config import SenseConfig
from roxabi_sense.query import SenseQuery


def build_mcp_server(cfg: SenseConfig):
    """Register tools on MCPServer; lazy-import SDK (optional dependency)."""
    try:
        from mcp.server import MCPServer
    except ImportError as exc:
        raise ImportError("MCP SDK not installed. Run: uv sync --extra mcp") from exc

    q = SenseQuery.from_config(cfg)
    mcp = MCPServer(
        name="roxabi-sense",
        instructions=(
            "Local workstation attention sensor. Tools read the local SQLite "
            "store (facts only). Default redaction is coarse (no window titles "
            "/ media / full paths) unless operator config sets mcp.detail=full."
        ),
    )

    @mcp.tool()
    def sense_status() -> dict[str, Any]:
        """Daemon health, presence, last collect meta."""
        return q.sense_status()

    @mcp.tool()
    def active_now() -> dict[str, Any]:
        """Current presence, latest focus app, open agent sessions."""
        return q.active_now()

    @mcp.tool()
    def what_was_i_doing(day: str | None = None, limit: int = 50) -> dict[str, Any]:
        """Timeline for a local calendar day (YYYY-MM-DD); default today.

        Returns summarized events (coarse by default). limit caps event count.
        """
        return q.what_was_i_doing(day, limit=limit)

    @mcp.tool()
    def agent_sessions(day: str | None = None) -> dict[str, Any]:
        """Claude/Grok sessions seen during a local calendar day."""
        return q.agent_sessions(day)

    @mcp.tool()
    def day_recap(day: str | None = None) -> dict[str, Any]:
        """Compiled day recap (apps, away, meetings, agents) as JSON."""
        return q.day_recap(day)

    return mcp


def run_mcp(cfg: SenseConfig, *, transport: str = "stdio") -> int:
    """Process entry for `sense mcp` (default stdio for host agents)."""
    try:
        server = build_mcp_server(cfg)
    except ImportError as exc:
        print(f"sense mcp: {exc}", file=sys.stderr)
        return 2
    # transport: stdio (V1) | streamable-http / sse (future remote / API-adjacent)
    if transport == "stdio":
        server.run(transport="stdio")
        return 0
    if transport in {"streamable-http", "sse"}:
        # Documented hook for remote MCP; not the default product path yet.
        server.run(transport=transport)  # type: ignore[arg-type]
        return 0
    print(f"sense mcp: unknown transport {transport!r}", file=sys.stderr)
    return 2


def tool_catalog() -> list[dict[str, str]]:
    """Static tool list for docs / HTTP OpenAPI mapping (no SDK required)."""
    return [
        {
            "name": "sense_status",
            "http": "GET /v1/status",
            "returns": "daemon health + presence",
        },
        {
            "name": "active_now",
            "http": "GET /v1/active",
            "returns": "presence + focus + agent sessions",
        },
        {
            "name": "what_was_i_doing",
            "http": "GET /v1/timeline?day=&limit=",
            "returns": "day event summaries",
        },
        {
            "name": "agent_sessions",
            "http": "GET /v1/sessions?day=",
            "returns": "agent sessions for day",
        },
        {
            "name": "day_recap",
            "http": "GET /v1/recap?day=",
            "returns": "compiled day recap JSON",
        },
    ]


def tool_catalog_json() -> str:
    return json.dumps(tool_catalog(), indent=2)
