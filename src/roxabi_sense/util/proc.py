"""Low-level /proc helpers (no agent/tmux join logic)."""

from __future__ import annotations

from collections import deque
from pathlib import Path


def read_comm(pid: int) -> str | None:
    try:
        raw = Path(f"/proc/{pid}/comm").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return raw or None


def read_cwd(pid: int) -> str | None:
    """Resolved cwd (same as Path.resolve) for consistent joins."""
    try:
        return str(Path(f"/proc/{pid}/cwd").resolve())
    except OSError:
        return None


def resolve_app_name(app: str, pid: int | None) -> str:
    """Map AT-SPI 'Unnamed' to real process name (e.g. ghostty)."""
    cleaned = (app or "").strip()
    if cleaned and cleaned not in {"Unnamed", "unknown", "Unknown"}:
        return cleaned
    if pid is None:
        return cleaned or "unknown"
    comm = read_comm(pid)
    return comm or cleaned or "unknown"


def children_map() -> dict[int, list[int]]:
    """Build parent_pid → [child_pid] from /proc (call once per tick)."""
    tree: dict[int, list[int]] = {}
    proc = Path("/proc")
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            stat = (entry / "stat").read_text(encoding="utf-8")
            rparen = stat.rfind(")")
            if rparen < 0:
                continue
            rest = stat[rparen + 2 :].split()
            if len(rest) < 2:
                continue
            ppid = int(rest[1])
        except (OSError, ValueError, IndexError):
            continue
        tree.setdefault(ppid, []).append(pid)
    return tree


def descendants(
    root_pid: int,
    *,
    limit: int = 200,
    tree: dict[int, list[int]] | None = None,
) -> list[int]:
    """BFS descendants of root_pid (not including root)."""
    cmap = tree if tree is not None else children_map()
    out: list[int] = []
    queue: deque[int] = deque(cmap.get(root_pid, []))
    seen = {root_pid}
    while queue and len(out) < limit:
        pid = queue.popleft()
        if pid in seen:
            continue
        seen.add(pid)
        out.append(pid)
        queue.extend(cmap.get(pid, []))
    return out
