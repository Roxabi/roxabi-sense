"""MCP surface registration (optional SDK)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from roxabi_sense.config import SenseConfig
from roxabi_sense.surfaces.mcp import build_mcp_server, tool_catalog

mcp = pytest.importorskip("mcp")


def test_tool_catalog_http_mapping() -> None:
    names = {t["name"] for t in tool_catalog()}
    assert names == {
        "sense_status",
        "active_now",
        "what_was_i_doing",
        "agent_sessions",
        "day_recap",
    }
    for t in tool_catalog():
        assert t["http"].startswith("GET /v1/")


def test_build_mcp_server_registers_tools(tmp_path: Path) -> None:
    cfg = SenseConfig(db_path=tmp_path / "s.db")
    server = build_mcp_server(cfg)
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "sense_status",
        "active_now",
        "what_was_i_doing",
        "agent_sessions",
        "day_recap",
    }
