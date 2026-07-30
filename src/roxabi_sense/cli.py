"""CLI entrypoint."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from roxabi_sense import __version__
from roxabi_sense.config import load_config
from roxabi_sense.daemon import collect_once, run_daemon
from roxabi_sense.paths import default_config_path
from roxabi_sense.store import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sense",
        description="roxabi-sense — workstation attention journal",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=f"config.toml (default: {default_config_path()})",
    )
    sub = parser.add_subparsers(dest="cmd")

    p_status = sub.add_parser("status", help="Store / last tick status")
    p_status.add_argument(
        "--collect",
        action="store_true",
        help="Run one collect cycle before printing status",
    )

    p_day = sub.add_parser("day", help="Day timeline")
    p_day.add_argument(
        "--date",
        dest="day",
        default=None,
        help="YYYY-MM-DD (local day, default: today)",
    )
    p_day.add_argument("--json", action="store_true", help="JSON lines output")
    p_day.add_argument("--limit", type=int, default=200, help="Max events to show")

    sub.add_parser("daemon", help="Run collectors in foreground")
    sub.add_parser("mcp", help="Run MCP stdio server (not implemented)")
    sub.add_parser("install-service", help="Install systemd --user unit (not implemented)")

    p_once = sub.add_parser("once", help="Single collect tick then exit")
    _ = p_once

    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 0

    cfg = load_config(args.config)

    if args.cmd == "daemon":
        return run_daemon(cfg)
    if args.cmd == "once":
        n = collect_once(cfg)
        print(f"wrote {n} events → {cfg.db_path}")
        return 0
    if args.cmd == "status":
        if args.collect:
            collect_once(cfg)
        return cmd_status(cfg.db_path)
    if args.cmd == "day":
        return cmd_day(cfg.db_path, day=args.day, as_json=args.json, limit=args.limit)
    if args.cmd in {"mcp", "install-service"}:
        print(
            f"sense {__version__}: command '{args.cmd}' not implemented yet",
            file=sys.stderr,
        )
        return 2

    parser.print_help()
    return 0


def cmd_status(db_path: Path) -> int:
    if not db_path.is_file():
        print(f"db: missing ({db_path})")
        print("hint: run `sense once` or `sense daemon`")
        return 0
    store = Store(db_path)
    print(f"db: {db_path}")
    print(f"events: {store.count()}")
    print(f"last_tick: {store.get_meta('last_tick') or '—'}")
    print(f"daemon_started: {store.get_meta('daemon_started') or '—'}")
    print(f"machine: {store.get_meta('machine') or '—'}")
    last = store.last_event()
    if last:
        payload_preview = json.dumps(last.payload, ensure_ascii=False)[:120]
        print(f"last_event: {last.ts} {last.kind} {payload_preview}")
    for kind in (
        "agent_sessions_snapshot",
        "process_snapshot",
        "idle",
        "media_snapshot",
        "tmux_snapshot",
        "desktop_snapshot",
        "focus",
    ):
        ev = store.last_by_kind(kind)
        if ev:
            preview = json.dumps(ev.payload, ensure_ascii=False)[:100]
            print(f"  {kind}: {ev.ts} {preview}")
    store.close()
    return 0


def cmd_day(db_path: Path, *, day: str | None, as_json: bool, limit: int) -> int:
    if not db_path.is_file():
        print(f"db: missing ({db_path})", file=sys.stderr)
        return 1
    store = Store(db_path)
    start, end = store.day_bounds(day)
    # Prefer snapshot kinds for readable day view
    events = store.events_between(start, end, limit=limit * 3)
    interesting = {
        "agent_sessions_snapshot",
        "agent_session",
        "process_snapshot",
        "idle",
        "media",
        "media_snapshot",
        "tmux_snapshot",
        "focus",
        "desktop_snapshot",
    }
    filtered = [e for e in events if e.kind in interesting][:limit]
    if as_json:
        for e in filtered:
            row = {"ts": e.ts, "kind": e.kind, "payload": e.payload}
            print(json.dumps(row, ensure_ascii=False))
    else:
        label = day or "today"
        print(f"sense day ({label})  {start} → {end}  n={len(filtered)}")
        for e in filtered:
            summary = _summarize(e.kind, e.payload)
            print(f"{e.ts}  {e.kind:24}  {summary}")
    store.close()
    return 0


def _summarize(kind: str, payload: dict) -> str:
    if kind == "agent_sessions_snapshot":
        sessions = payload.get("sessions") or []
        bits = []
        for s in sessions[:6]:
            if not isinstance(s, dict):
                continue
            bits.append(f"{s.get('agent')}:{s.get('cwd') or s.get('state')}")
        return f"n={payload.get('count')} " + "; ".join(bits)
    if kind == "tmux_snapshot":
        panes = payload.get("panes") or []
        bits = [
            f"{p.get('session')}:{p.get('command')}@{p.get('path')}"
            for p in panes[:6]
            if isinstance(p, dict)
        ]
        return f"n={payload.get('count')} " + "; ".join(bits)
    if kind == "process_snapshot":
        procs = payload.get("processes") or {}
        running = [k for k, v in procs.items() if isinstance(v, dict) and v.get("running")]
        return "running=" + ",".join(running)
    if kind == "idle":
        return f"idle={payload.get('idle')} locked={payload.get('locked')}"
    if kind == "media":
        player = payload.get("player")
        status = payload.get("status")
        artist = payload.get("artist")
        title = payload.get("title")
        return f"{player} {status}: {artist} — {title}"
    if kind == "media_snapshot":
        players = payload.get("players") or []
        return f"players={len(players)}"
    if kind == "agent_session":
        return f"{payload.get('agent')} {payload.get('cwd') or payload.get('state')}"
    if kind == "focus":
        return f"{payload.get('app')}: {payload.get('title')}"
    if kind == "desktop_snapshot":
        focus = payload.get("focus") or {}
        n = len(payload.get("windows") or [])
        return f"n={n} focus={focus.get('app')}: {focus.get('title')}"
    return json.dumps(payload, ensure_ascii=False)[:100]



if __name__ == "__main__":
    raise SystemExit(main())
