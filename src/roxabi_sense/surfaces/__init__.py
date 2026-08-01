"""Secondary adapters: CLI, MCP, optional NATS — format only over store/report."""

from roxabi_sense.surfaces.cli import main as cli_main

__all__ = ["cli_main"]

# MCP entry: roxabi_sense.surfaces.mcp.run_mcp (optional dep `mcp`)
