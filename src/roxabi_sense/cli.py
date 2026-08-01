"""CLI entrypoint — re-exports surfaces.cli (compat for scripts + tests)."""

from __future__ import annotations

from roxabi_sense.surfaces.cli import cmd_day, cmd_recap, main

__all__ = ["cmd_day", "cmd_recap", "main"]

if __name__ == "__main__":
    raise SystemExit(main())
