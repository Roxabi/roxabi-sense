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
        "care_brief",
        "day_recap",
        "top_apps",
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
        "care_brief",
        "day_recap",
        "top_apps",
    }
    resources = asyncio.run(server.list_resources())
    uris = {str(getattr(r, "uri", r)) for r in resources}
    assert any("care-brief" in u for u in uris)


def test_care_brief_schema_lists_db_exists() -> None:
    src = Path(__file__).resolve().parents[1] / "src/roxabi_sense/surfaces/mcp.py"
    blob = src.read_text(encoding="utf-8")
    schema = blob.split("def care_brief_schema")[1].split("return mcp")[0]
    assert '"db_exists"' in schema
