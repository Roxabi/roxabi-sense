"""Append-only AT-SPI diagnostic JSONL (outside recap store)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from roxabi_sense.paths import xdg_data_home


def default_trace_path() -> Path:
    return xdg_data_home() / "roxabi-sense" / "atspi-trace.jsonl"


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


@dataclass
class AtspiTraceWriter:
    """Write raw AT-SPI diagnostics for empirical focus redesign."""

    path: Path
    hours: float = 48.0
    _started_mono: float = 0.0
    _started_wall: str = ""
    _n: int = 0
    _closed: bool = False

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._started_mono = time.monotonic()
        self._started_wall = _utc_now()
        self._write(
            {
                "type": "trace_begin",
                "ts": self._started_wall,
                "hours": self.hours,
                "path": str(self.path),
                "note": (
                    "Empirical AT-SPI capture: event source vs multi-ACTIVE inventory. "
                    "Not used by recap."
                ),
            }
        )

    @property
    def active(self) -> bool:
        if self._closed:
            return False
        return (time.monotonic() - self._started_mono) < (self.hours * 3600.0)

    def write(self, record: dict[str, Any]) -> None:
        if not self.active:
            if not self._closed:
                self._write(
                    {
                        "type": "trace_end",
                        "ts": _utc_now(),
                        "reason": "duration_elapsed",
                        "records": self._n,
                    }
                )
                self._closed = True
            return
        row = dict(record)
        row.setdefault("ts", _utc_now())
        self._write(row)

    def _write(self, row: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        self._n += 1


def summarize_trace(path: Path, *, limit_examples: int = 5) -> dict[str, Any]:
    """Cheap offline summary for post-48h analysis."""
    from collections import Counter

    path = Path(path)
    if not path.is_file():
        return {"error": "missing", "path": str(path)}
    n = 0
    by_type: Counter[str] = Counter()
    by_src_app: Counter[str] = Counter()
    multi_active = 0
    disagree = 0  # source app not in actives / not matching first active
    examples: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            n += 1
            typ = str(row.get("type") or "?")
            by_type[typ] += 1
            if typ != "atspi_raw":
                continue
            src_raw = row.get("source")
            src: dict[str, Any] = src_raw if isinstance(src_raw, dict) else {}
            app = str(src.get("app") or "?")
            by_src_app[app] += 1
            act_raw = row.get("actives")
            actives: list[Any] = act_raw if isinstance(act_raw, list) else []
            if len(actives) > 1:
                multi_active += 1
            first_obj = actives[0] if actives and isinstance(actives[0], dict) else None
            first = first_obj.get("app") if isinstance(first_obj, dict) else None
            ev = str(row.get("event") or "")
            if first and app and first != app and "activate" in ev:
                disagree += 1
                if len(examples) < limit_examples:
                    apps = [
                        a.get("app") for a in actives if isinstance(a, dict)
                    ][:8]
                    examples.append(
                        {
                            "ts": row.get("ts"),
                            "event": row.get("event"),
                            "source_app": app,
                            "first_active": first,
                            "n_actives": len(actives),
                            "actives": apps,
                        }
                    )
    return {
        "path": str(path),
        "lines": n,
        "by_type": dict(by_type.most_common()),
        "source_app_top": by_src_app.most_common(15),
        "multi_active_raw_events": multi_active,
        "activate_source_vs_first_active_disagree": disagree,
        "examples_disagree": examples,
    }
