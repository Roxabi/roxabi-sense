"""Explicit browser connector installation and native-host entry point."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from roxabi_sense.collectors.meeting_browser.installer import (
    export_extension,
    install,
    remove,
    validate_installation,
)
from roxabi_sense.config import SenseConfig
from roxabi_sense.paths import default_config_path, xdg_config_home


def add_arguments(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("meeting-browser", help="Manage the Meet browser connector")
    commands = parser.add_subparsers(dest="meeting_browser_action", required=True)
    export = commands.add_parser("export", help="Export the unpacked extension")
    export.add_argument("destination", type=Path)
    for action in ("install", "remove"):
        command = commands.add_parser(action, help=f"{action.capitalize()} the native host")
        command.add_argument("--browser", choices=("chrome", "chromium"), required=True)
        command.add_argument("--profile", type=Path, required=True)
        command.add_argument("--extension-id", required=True)
    host = commands.add_parser("host", help=argparse.SUPPRESS)
    host.add_argument("--profile", type=Path, required=True)
    host.add_argument("--source-id", required=True)
    host.add_argument("--parent-window", type=_parent_window)
    host.add_argument("--extension-id", required=True)
    host.add_argument("origin")


def _parent_window(value: str) -> str:
    if not re.fullmatch(r"(?:0x[0-9a-fA-F]+|[0-9]+)", value):
        raise argparse.ArgumentTypeError("invalid parent window handle")
    return value


def dispatch(args: argparse.Namespace, cfg: SenseConfig) -> int:
    try:
        if args.meeting_browser_action in ("install", "remove"):
            root = xdg_config_home() / ("google-chrome" if args.browser == "chrome" else "chromium")
            profile_parent = Path(os.path.abspath(args.profile.expanduser())).parent
            if profile_parent != Path(os.path.abspath(root)):
                raise ValueError(f"profile must be directly inside {root}")
        if args.meeting_browser_action == "export":
            print(f"Exported extension to {export_extension(args.destination)}")
        elif args.meeting_browser_action == "install":
            install(cfg, profile=args.profile, extension_id=args.extension_id,
                    config_path=args.config or default_config_path())
            print("Native host installed; enable the collector explicitly in config.")
        elif args.meeting_browser_action == "remove":
            remove(cfg, profile=args.profile, extension_id=args.extension_id)
            print("Native host removed.")
        else:
            owner = validate_installation(cfg, profile=args.profile, extension_id=args.extension_id)
            if owner["source_id"] != args.source_id:
                raise ValueError("native host source does not match installation")
            from roxabi_sense.collectors.meeting_browser.host import run_host

            return run_host(cfg, args.source_id, args.extension_id, args.origin)
    except (OSError, ValueError) as exc:
        print(f"sense meeting-browser: {exc}", file=sys.stderr)
        return 2
    return 0
