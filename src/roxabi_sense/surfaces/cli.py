"""CLI surface — argparse + print over report/store (no query ownership)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from roxabi_sense import __version__
from roxabi_sense.config import ConfigError, load_config
from roxabi_sense.daemon import collect_once, run_daemon
from roxabi_sense.install_service import install_service
from roxabi_sense.paths import default_config_path
from roxabi_sense.report import (
    StatusSnapshot,
    compile_day_recap,
    format_day_recap,
    format_presence_lines,
    load_status_snapshot,
    summarize_event,
)
from roxabi_sense.store import DEFAULT_DAY_LIMIT, Store, clamp_event_limit


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
    sub.add_parser("mcp", help="Run MCP stdio server (uv sync --extra mcp)")
    p_doc = sub.add_parser("doctor", help="Install + daemon + MCP readiness checks")
    p_doc.add_argument("--json", action="store_true", help="JSON report")
    sub.add_parser("install-service", help="Install systemd --user unit")
    sub.add_parser("once", help="Single collect tick then exit")
    p_tr = sub.add_parser("atspi-trace", help="AT-SPI empirical JSONL trace status")
    p_tr.add_argument("--path", type=Path, default=None, help="trace jsonl path")
    p_tr.add_argument("--json", action="store_true", help="JSON summary")

    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 0

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"sense: {exc}", file=sys.stderr)
        return 2

    if args.cmd == "daemon":
        return run_daemon(cfg)
    if args.cmd == "once":
        n = collect_once(cfg)
        print(f"wrote {n} events → {cfg.db_path}")
        return 0
    if args.cmd == "status":
        if args.collect:
            collect_once(cfg)
        snap = load_status_snapshot(
            cfg.db_path,
            offline_threshold_s=cfg.offline_threshold_s,
            idle_threshold_s=cfg.idle_threshold_s,
        )
        return _print_status(snap, as_json=args.json)
    if args.cmd == "day":
        return cmd_day(cfg.db_path, day=args.day, as_json=args.json, limit=args.limit)
    if args.cmd == "recap":
        return cmd_recap(cfg.db_path, day=args.day, as_json=args.json)
    if args.cmd == "install-service":
        code, msg = install_service()
        print(msg)
        return code
    if args.cmd == "mcp":
        from roxabi_sense.surfaces.mcp import run_mcp

        return run_mcp(cfg, transport="stdio")
    if args.cmd == "doctor":
        from roxabi_sense.doctor import doctor_exit_code, format_doctor_text, run_doctor

        report = run_doctor(cfg)
        if args.json:
            print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(format_doctor_text(report))
        return doctor_exit_code(report)
    if args.cmd == "atspi-trace":
        from roxabi_sense.atspi.trace_log import default_trace_path, summarize_trace

        path = args.path or cfg.atspi_trace_path or default_trace_path()
        summary = summarize_trace(path)
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            if summary.get("error"):
                print(f"trace: missing ({path})")
                print("hint: set [daemon] atspi_trace=true and restart sense")
                return 1
            print(f"path: {summary['path']}")
            print(f"lines: {summary['lines']}")
            print(f"by_type: {summary.get('by_type')}")
            print(f"source_app_top: {summary.get('source_app_top')}")
            print(f"multi_active_raw_events: {summary.get('multi_active_raw_events')}")
            print(
                "activate_source_vs_first_active_disagree: "
                f"{summary.get('activate_source_vs_first_active_disagree')}"
            )
            for ex in summary.get("examples_disagree") or []:
                print(f"  e.g. {ex}")
        return 0

    parser.print_help()
    return 0


def _print_status(snap: StatusSnapshot, *, as_json: bool) -> int:
    meeting = _status_meeting_annotation(snap)
    if not snap.db_exists:
        print(f"db: missing ({snap.db_path})")
        print("hint: run `sense once` or `sense daemon`")
        if as_json:
            body = {
                "db": str(snap.db_path),
                "presence": snap.presence.to_dict(),
                "annotations": {"meeting": meeting},
            }
            print(json.dumps(body, indent=2))
        else:
            print("\n".join(format_presence_lines(snap.presence)))
        return 0
    if as_json:
        # Legacy CLI keys + meeting annotation (ADR-004 live path)
        body = {
            "db": str(snap.db_path),
            "events": snap.events,
            "last_tick": snap.last_tick,
            "daemon_started": snap.daemon_started,
            "machine": snap.machine,
            "presence": snap.presence.to_dict(),
            "annotations": {"meeting": meeting},
        }
        print(json.dumps(body, ensure_ascii=False, indent=2))
        return 0
    print(f"db: {snap.db_path}")
    print(f"events: {snap.events}")
    print(f"last_tick: {snap.last_tick or '—'}")
    print(f"daemon_started: {snap.daemon_started or '—'}")
    print(f"machine: {snap.machine or '—'}")
    print("--- presence ---")
    for line in format_presence_lines(snap.presence):
        print(line)
    print("--- meeting ---")
    phase = meeting.get("phase")
    fid = meeting.get("fidelity") or "unknown"
    if phase:
        print(f"phase: {phase}  fidelity={fid}")
        if meeting.get("provider"):
            print(f"provider: {meeting['provider']}  since: {meeting.get('start') or '—'}")
    else:
        print(f"phase: none  fidelity={fid}")
    note = meeting.get("fidelity_note")
    if note:
        print(f"note: {note}")
    last = snap.last_event
    if last:
        payload_preview = json.dumps(last.payload, ensure_ascii=False)[:120]
        print(f"last_event: {last.ts} {last.kind} {payload_preview}")
    for kind, ev in snap.latest_by_kind.items():
        preview = json.dumps(ev.payload, ensure_ascii=False)[:100]
        print(f"  {kind}: {ev.ts} {preview}")
    return 0


def _status_meeting_annotation(snap: StatusSnapshot) -> dict:
    """Live meeting annotation via report compiler (surfaces format only)."""
    from roxabi_sense.report.meeting_sessions import meeting_annotation_now

    if not snap.db_exists or not snap.db_path.is_file():
        return {"phase": None, "fidelity": "unknown", "fidelity_note": "no store"}
    try:
        with Store(snap.db_path) as store:
            ev = store.events_for_day(
                None,
                kinds=("focus", "desktop_snapshot"),
                limit=20_000,
            )
            return meeting_annotation_now(
                ev,
                focus_backend=store.get_meta("focus_backend"),
            )
    except Exception:  # noqa: BLE001
        return {
            "phase": None,
            "fidelity": "unknown",
            "fidelity_note": "meeting annotation unavailable",
        }


def cmd_day(db_path: Path, *, day: str | None, as_json: bool, limit: int) -> int:
    if not db_path.is_file():
        print(f"db: missing ({db_path})", file=sys.stderr)
        return 1
    lim = clamp_event_limit(limit, default=DEFAULT_DAY_LIMIT)
    try:
        with Store(db_path) as store:
            start, end = store.day_bounds(day)
            events = store.events_for_day(day, limit=lim)
    except ValueError as exc:
        print(f"sense day: {exc}", file=sys.stderr)
        return 2
    if as_json:
        for e in events:
            row = {"ts": e.ts, "kind": e.kind, "payload": e.payload}
            print(json.dumps(row, ensure_ascii=False))
    else:
        print(f"sense day ({day or 'today'})  {start} → {end}  n={len(events)}")
        for e in events:
            print(f"{e.ts}  {e.kind:24}  {summarize_event(e.kind, e.payload)}")
        if len(events) >= lim:
            msg = f"(capped at {lim}; use `sense recap` for a compiled day summary)"
            print(msg, file=sys.stderr)
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


if __name__ == "__main__":
    raise SystemExit(main())
