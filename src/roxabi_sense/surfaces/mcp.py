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
            "Local workstation attention sensor. Heartbeat: call care_brief. "
            "Default redaction is coarse (no window titles / media / full paths)."
        ),
    )

    @mcp.tool()
    def sense_status() -> dict[str, Any]:
        """Daemon health + presence (includes degraded_reason). No window titles."""
        return q.sense_status()

    @mcp.tool()
    def active_now() -> dict[str, Any]:
        """Current presence + focused app name. No window titles."""
        return q.active_now()

    @mcp.tool()
    def what_was_i_doing(day: str | None = None, limit: int = 50) -> dict[str, Any]:
        """Timeline for a local day (YYYY-MM-DD). Coarse summaries; not for heartbeat."""
        return q.what_was_i_doing(day, limit=limit)

    @mcp.tool()
    def agent_sessions(day: str | None = None) -> dict[str, Any]:
        """Claude/Grok sessions for a local day. No transcripts."""
        return q.agent_sessions(day)

    @mcp.tool()
    def care_brief(day: str | None = None) -> dict[str, Any]:
        """Heartbeat day brief: apps/presence/shape. No window titles."""
        return q.care_brief(day)

    @mcp.tool()
    def day_recap(day: str | None = None) -> dict[str, Any]:
        """Coarse day recap. Heartbeat uses care_brief. No detail=segments."""
        return q.day_recap(day)

    @mcp.tool()
    def top_apps(day: str | None = None, limit: int = 20) -> dict[str, Any]:
        """Ranked app minutes for a local day. No window titles."""
        return q.top_apps(day, limit=limit)

    @mcp.resource("sense://care-brief/schema", mime_type="application/json")
    def care_brief_schema() -> str:
        return json.dumps(
            {
                "title": "care_brief",
                "privacy": "no window titles",
                "fields": [
                    "day",
                    "first_event",
                    "last_event",
                    "presence",
                    "tracked_minutes",
                    "away_minutes",
                    "idle_events",
                    "top_apps",
                    "focus_repos",
                    "agent_repos",
                    "focus_switches",
                    "longest_focus_app",
                    "current_stretch",
                    "last_away",
                    "last_pause",
                    "minutes_since_pause",
                    "terminal_stays",
                    "meetings",
                    "agent_sessions",
                    "agent_sessions_reason",
                    "shape",
                    "signals",
                    "db_exists",
                ],
                "shape": ["focused", "fragmented", "drifted", "away", "unknown"],
                "repos": {
                    "focus_repos": "your terminal focus per repo (Herdr focused pane)",
                    "agent_repos": "Herdr agents `working` per repo; unfocused_minutes = "
                    "while your focus was elsewhere; now = pane statuses",
                    "agent_repos[].sessions": "per agent session: session_id, title "
                    "(session auto-title), working_minutes, now (Herdr status)",
                    "current_stretch.repo": "repo in front when the stretch is a terminal",
                },
            }
        )

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
            "returns": "presence + focus app name",
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
            "name": "care_brief",
            "http": "GET /v1/brief?day=",
            "returns": "heartbeat day brief (no titles)",
        },
        {
            "name": "day_recap",
            "http": "GET /v1/recap?day=",
            "returns": "coarse day recap (not care_brief; no segments arg)",
        },
        {
            "name": "top_apps",
            "http": "GET /v1/top-apps?day=&limit=",
            "returns": "ranked app seconds/minutes for day",
        },
    ]


def tool_catalog_json() -> str:
    return json.dumps(tool_catalog(), indent=2)
