"""Process helpers: comm/cwd, descendants, agent session join."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

_GROK_SESSIONS = Path.home() / ".grok" / "active_sessions.json"
_AGENT_COMMS = frozenset({"grok", "claude"})
_TMUX = next(
    (p for p in ("/usr/bin/tmux", "/usr/local/bin/tmux") if Path(p).is_file()),
    None,
)


def read_comm(pid: int) -> str | None:
    try:
        raw = Path(f"/proc/{pid}/comm").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return raw or None


def read_cwd(pid: int) -> str | None:
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


def _children_map() -> dict[int, list[int]]:
    """Build parent_pid → [child_pid] from /proc."""
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


def descendants(root_pid: int, *, limit: int = 200) -> list[int]:
    """BFS descendants of root_pid (not including root)."""
    tree = _children_map()
    out: list[int] = []
    queue = list(tree.get(root_pid, []))
    seen = {root_pid}
    while queue and len(out) < limit:
        pid = queue.pop(0)
        if pid in seen:
            continue
        seen.add(pid)
        out.append(pid)
        queue.extend(tree.get(pid, []))
    return out


def load_grok_sessions(path: Path | None = None) -> list[dict[str, Any]]:
    p = path or _GROK_SESSIONS
    if not p.is_file():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    sessions: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        sessions.append(
            {
                "agent": "grok",
                "session_id": item.get("session_id"),
                "pid": item.get("pid"),
                "cwd": item.get("cwd"),
                "opened_at": item.get("opened_at"),
            }
        )
    return sessions


def _session_by_pid(sessions: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    by_pid: dict[int, dict[str, Any]] = {}
    for s in sessions:
        pid = s.get("pid")
        if isinstance(pid, int):
            by_pid[pid] = s
        elif isinstance(pid, str) and pid.isdigit():
            by_pid[int(pid)] = s
    return by_pid


def _link_from_session(s: dict[str, Any], *, match: str, pid: int | None = None) -> dict[str, Any]:
    return {
        "agent": "grok",
        "session_id": s.get("session_id"),
        "cwd": s.get("cwd"),
        "pid": pid if pid is not None else s.get("pid"),
        "match": match,
    }


def _find_via_process_tree(
    window_pid: int,
    sessions: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Works when the agent is a child of the window process (not Ghostty→tmux)."""
    by_pid = _session_by_pid(sessions)
    for pid in [window_pid, *descendants(window_pid)]:
        if pid in by_pid:
            return _link_from_session(by_pid[pid], match="session_pid", pid=pid)
        comm = read_comm(pid)
        if comm not in _AGENT_COMMS:
            continue
        cwd = read_cwd(pid)
        for s in sessions:
            if s.get("cwd") and cwd and s.get("cwd") == cwd:
                return _link_from_session(s, match="cwd", pid=pid)
        return {
            "agent": comm,
            "session_id": None,
            "cwd": cwd,
            "pid": pid,
            "match": "comm",
        }
    return None


def list_tmux_agent_panes() -> list[dict[str, Any]]:
    """Panes whose current command is grok/claude."""
    if not _TMUX:
        return []
    fmt = "#{pane_pid}\t#{pane_current_command}\t#{pane_current_path}\t#{session_attached}"
    try:
        proc = subprocess.run(
            [_TMUX, "list-panes", "-a", "-F", fmt],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    panes: list[dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        pid_s, cmd, path, attached = parts[0], parts[1], parts[2], parts[3]
        if cmd not in _AGENT_COMMS:
            continue
        panes.append(
            {
                "pane_pid": int(pid_s) if pid_s.isdigit() else None,
                "command": cmd,
                "path": path,
                "attached": attached == "1",
            }
        )
    return panes


def _score_title_cwd(title: str, cwd: str) -> int:
    """Heuristic: does the window title mention the project folder?"""
    if not title or not cwd:
        return 0
    base = Path(cwd).name
    if not base:
        return 0
    t = title.lower()
    b = base.lower()
    score = 0
    if b in t:
        score += 10
    # "roxabi-sense" vs "Roxabi Sense"
    spaced = b.replace("-", " ").replace("_", " ")
    if spaced in t:
        score += 8
    # partial tokens length>=4
    for tok in re.split(r"[-_]", b):
        if len(tok) >= 4 and tok in t:
            score += 3
    return score


def _find_via_tmux_title(
    title: str,
    sessions: list[dict[str, Any]],
    *,
    panes: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """
    Ghostty attaches to tmux; agent is under tmux server, not Ghostty.

    Match active_sessions ↔ tmux grok panes by cwd/pid, rank by title≈project.
    """
    agent_panes = panes if panes is not None else list_tmux_agent_panes()
    if not agent_panes or not sessions:
        return None

    by_pid = _session_by_pid(sessions)
    by_cwd = {str(s.get("cwd")): s for s in sessions if s.get("cwd")}

    # (title_score, attached_bonus, session, match)
    scored: list[tuple[int, int, dict[str, Any], str]] = []
    for pane in agent_panes:
        path = pane.get("path") or ""
        pane_pid = pane.get("pane_pid")
        s = None
        match = "tmux_path"
        if isinstance(pane_pid, int) and pane_pid in by_pid:
            s = by_pid[pane_pid]
            match = "tmux_pane_pid"
        elif path in by_cwd:
            s = by_cwd[path]
            match = "tmux_path"
        else:
            # pane_pid may be shell; children may be grok session pid
            if isinstance(pane_pid, int):
                for d in descendants(pane_pid, limit=50):
                    if d in by_pid:
                        s = by_pid[d]
                        match = "tmux_child_pid"
                        break
        if s is None:
            continue
        title_score = _score_title_cwd(title, str(s.get("cwd") or path))
        attached_bonus = 1 if pane.get("attached") else 0
        scored.append((title_score, attached_bonus, s, match))

    if not scored:
        return None
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    best_score, _att, best_s, best_match = scored[0]
    unique_sessions = {str(s.get("session_id")) for _, _, s, _ in scored}
    # Need title signal when several sessions/panes are possible
    if best_score == 0 and len(unique_sessions) > 1:
        return None
    if best_score == 0 and len(unique_sessions) == 1:
        return _link_from_session(best_s, match=best_match)
    if best_score > 0:
        return _link_from_session(best_s, match=f"{best_match}+title")
    return None


def find_agent_link(
    window_pid: int | None,
    *,
    app: str | None = None,
    title: str | None = None,
    sessions: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """
    Link a focused window to a Grok/Claude session.

    1) Process tree under the window pid (direct embedding)
    2) Ghostty/tmux: score sessions by title ≈ project path
    """
    sess = sessions if sessions is not None else load_grok_sessions()
    if window_pid is not None:
        hit = _find_via_process_tree(window_pid, sess)
        if hit is not None:
            return hit

    app_l = (app or "").lower()
    title_s = title or ""
    looks_agent_term = (
        app_l in {"ghostty", "unnamed", "unknown"}
        or title_s.lower().endswith("- grok")
        or " grok" in title_s.lower()
        or "claude" in title_s.lower()
    )
    if looks_agent_term and title_s:
        return _find_via_tmux_title(title_s, sess)
    return None
