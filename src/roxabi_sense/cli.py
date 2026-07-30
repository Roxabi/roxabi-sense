"""CLI entrypoint — thin surface over store queries."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from roxabi_sense import __version__
from roxabi_sense.config import load_config
from roxabi_sense.daemon import collect_once, run_daemon
from roxabi_sense.install_service import install_service
from roxabi_sense.paths import default_config_path
from roxabi_sense.report import (
    compile_day_recap,
    format_day_recap,
    format_presence_lines,
    presence_from_store,
)
from roxabi_sense.store import STATUS_KINDS, Store


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

    p_status = sub.add_parser("status", help="Store / presence status")
    p_status.add_argument(
        "--collect",
        action="store_true",
        help="Run one collect cycle before printing status",
    )
    p_status.add_argument("--json", action="store_true", help="JSON presence + meta")

    p_day = sub.add_parser("day", help="Day timeline (raw events)")
    p_day.add_argument(
        "--date",
        dest="day",
        default=None,
        help="YYYY-MM-DD (local day, default: today)",
    )
    p_day.add_argument("--json", action="store_true", help="JSON lines output")
    p_day.add_argument("--limit", type=int, default=200, help="Max events to show")

    p_recap = sub.add_parser("recap", help="Compiled day recap (focus / repos / agents)")
    p_recap.add_argument(
        "--date",
        dest="day",
        default=None,
        help="YYYY-MM-DD (local day, default: today)",
    )
    p_recap.add_argument("--json", action="store_true", help="JSON object output")

    sub.add_parser("daemon", help="Run collectors in foreground")
    sub.add_parser("mcp", help="Run MCP stdio server (not implemented)")
    sub.add_parser("install-service", help="Install systemd --user unit")
    sub.add_parser("once", help="Single collect tick then exit")

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
        return cmd_status(
            cfg.db_path,
            as_json=args.json,
            offline_threshold_s=cfg.offline_threshold_s,
            idle_threshold_s=cfg.idle_threshold_s,
        )
    if args.cmd == "day":
        return cmd_day(cfg.db_path, day=args.day, as_json=args.json, limit=args.limit)
    if args.cmd == "recap":
        return cmd_recap(cfg.db_path, day=args.day, as_json=args.json)
    if args.cmd == "install-service":
        code, msg = install_service()
        print(msg)
        return code
    if args.cmd == "mcp":
        print(
            f"sense {__version__}: command 'mcp' not implemented yet",
            file=sys.stderr,
        )
        return 2

    parser.print_help()
    return 0


def cmd_status(
    db_path: Path,
    *,
    as_json: bool = False,
    offline_threshold_s: float = 120.0,
    idle_threshold_s: float = 300.0,
) -> int:
    if not db_path.is_file():
        print(f"db: missing ({db_path})")
        print("hint: run `sense once` or `sense daemon`")
        # Still emit offline presence shape for agents
        from roxabi_sense.report.presence import derive_presence

        p = derive_presence(
            last_tick=None,
            idle_watch="n/a",
            offline_threshold_s=offline_threshold_s,
            idle_threshold_s=idle_threshold_s,
        )
        if as_json:
            print(json.dumps({"db": str(db_path), "presence": p.to_dict()}, indent=2))
        else:
            print("\n".join(format_presence_lines(p)))
        return 0
    with Store(db_path) as store:
        presence = presence_from_store(
            store,
            offline_threshold_s=offline_threshold_s,
            idle_threshold_s=idle_threshold_s,
        )
        if as_json:
            body = {
                "db": str(db_path),
                "events": store.count(),
                "last_tick": store.get_meta("last_tick"),
                "daemon_started": store.get_meta("daemon_started"),
                "machine": store.get_meta("machine"),
                "presence": presence.to_dict(),
            }
            print(json.dumps(body, ensure_ascii=False, indent=2))
            return 0
        print(f"db: {db_path}")
        print(f"events: {store.count()}")
        print(f"last_tick: {store.get_meta('last_tick') or '—'}")
        print(f"daemon_started: {store.get_meta('daemon_started') or '—'}")
        print(f"machine: {store.get_meta('machine') or '—'}")
        print("--- presence ---")
        for line in format_presence_lines(presence):
            print(line)
        last = store.last_event()
        if last:
            payload_preview = json.dumps(last.payload, ensure_ascii=False)[:120]
            print(f"last_event: {last.ts} {last.kind} {payload_preview}")
        for kind, ev in store.latest_by_kinds(STATUS_KINDS).items():
            preview = json.dumps(ev.payload, ensure_ascii=False)[:100]
            print(f"  {kind}: {ev.ts} {preview}")
    return 0


def cmd_day(db_path: Path, *, day: str | None, as_json: bool, limit: int) -> int:
    if not db_path.is_file():
        print(f"db: missing ({db_path})", file=sys.stderr)
        return 1
    try:
        with Store(db_path) as store:
            start, end = store.day_bounds(day)
            events = store.events_for_day(day, limit=limit)
    except ValueError as exc:
        print(f"sense day: {exc}", file=sys.stderr)
        return 2
    if as_json:
        for e in events:
            row = {"ts": e.ts, "kind": e.kind, "payload": e.payload}
            print(json.dumps(row, ensure_ascii=False))
    else:
        label = day or "today"
        print(f"sense day ({label})  {start} → {end}  n={len(events)}")
        for e in events:
            summary = _summarize(e.kind, e.payload)
            print(f"{e.ts}  {e.kind:24}  {summary}")
        if len(events) >= limit:
            print(
                f"(capped at {limit}; use `sense recap` for a compiled day summary)",
                file=sys.stderr,
            )
    return 0


def cmd_recap(db_path: Path, *, day: str | None, as_json: bool) -> int:
    if not db_path.is_file():
        print(f"db: missing ({db_path})", file=sys.stderr)
        return 1
    try:
        with Store(db_path) as store:
            recap = compile_day_recap(store, day)
    except ValueError as exc:
        print(f"sense recap: {exc}", file=sys.stderr)
        return 2
    if as_json:
        print(json.dumps(recap.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(format_day_recap(recap))
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
        return (
            f"idle={payload.get('idle')} source={payload.get('source')} "
            f"since={payload.get('idle_since')}"
        )
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
        agent = payload.get("agent") or {}
        agent_bit = ""
        if isinstance(agent, dict) and agent.get("agent"):
            agent_bit = f" → {agent.get('agent')}"
            if agent.get("cwd"):
                agent_bit += f"@{agent.get('cwd')}"
        return f"{payload.get('app')}: {payload.get('title')}{agent_bit}"
    if kind == "desktop_snapshot":
        focus = payload.get("focus") or {}
        n = len(payload.get("windows") or [])
        return f"n={n} focus={focus.get('app')}: {focus.get('title')}"
    return json.dumps(payload, ensure_ascii=False)[:100]


if __name__ == "__main__":
    raise SystemExit(main())
