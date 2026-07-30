"""CLI entrypoint — scaffold only."""

from __future__ import annotations

import argparse
import sys

from roxabi_sense import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sense",
        description="roxabi-sense — workstation attention journal",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("status", help="Daemon / store status (not implemented)")
    sub.add_parser("day", help="Day / time-slice report (not implemented)")
    sub.add_parser("mcp", help="Run MCP stdio server (not implemented)")
    sub.add_parser("install-service", help="Install systemd --user unit (not implemented)")
    sub.add_parser("daemon", help="Run collectors in foreground (not implemented)")

    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 0

    print(
        f"sense {__version__}: command '{args.cmd}' is scaffold-only — see docs/ARCHITECTURE.md",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
