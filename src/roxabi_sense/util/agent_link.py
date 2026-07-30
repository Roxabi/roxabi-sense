"""Join window focus → Grok/Claude session (tmux + process tree)."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from roxabi_sense.util.proc import children_map, descendants, read_comm, read_cwd

_GROK_SESSIONS = Path.home() / ".grok" / "active_sessions.json"
_AGENT_COMMS = frozenset({"grok", "claude"})
_TERMINAL_APPS = frozenset({"ghostty"})
_TMUX = next(
    (p for p in ("/usr/bin/tmux", "/usr/local/bin/tmux") if Path(p).is_file()),
    None,
)


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


def _link_from_session(
    s: dict[str, Any],
    *,
    match: str,
    pid: int | None = None,
) -> dict[str, Any]:
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
    *,
    tree: dict[int, list[int]],
) -> dict[str, Any] | None:
    """Prefer session_pid / cwd matches; bare comm only as last resort."""
    by_pid = _session_by_pid(sessions)
    candidates = [window_pid, *descendants(window_pid, tree=tree)]
    bare_comm: dict[str, Any] | None = None

    for pid in candidates:
        if pid in by_pid:
            return _link_from_session(by_pid[pid], match="session_pid", pid=pid)

    for pid in candidates:
        comm = read_comm(pid)
        if comm not in _AGENT_COMMS:
            continue
        cwd = read_cwd(pid)
        for s in sessions:
            scwd = s.get("cwd")
            if scwd and cwd and Path(str(scwd)).resolve() == Path(cwd):
                return _link_from_session(s, match="cwd", pid=pid)
            if scwd and cwd and str(scwd) == cwd:
                return _link_from_session(s, match="cwd", pid=pid)
        if bare_comm is None:
            bare_comm = {
                "agent": comm,
                "session_id": None,
                "cwd": cwd,
                "pid": pid,
                "match": "comm",
            }
    return bare_comm


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
    if not base or len(base) < 4:
        return 0
    t = f" {title.lower()} "
    b = base.lower()
    score = 0
    # word-ish boundary via spaces/punct around basename
    if re.search(rf"(?<![a-z0-9]){re.escape(b)}(?![a-z0-9])", t):
        score += 10
    spaced = b.replace("-", " ").replace("_", " ")
    if spaced != b and spaced in title.lower():
        score += 8
    for tok in re.split(r"[-_]", b):
        if len(tok) >= 6 and re.search(rf"(?<![a-z0-9]){re.escape(tok)}(?![a-z0-9])", t):
            score += 3
    return score


def _find_via_tmux_title(
    title: str,
    sessions: list[dict[str, Any]],
    *,
    panes: list[dict[str, Any]] | None = None,
    tree: dict[int, list[int]] | None = None,
) -> dict[str, Any] | None:
    agent_panes = panes if panes is not None else list_tmux_agent_panes()
    if not agent_panes or not sessions:
        return None

    by_pid = _session_by_pid(sessions)
    by_cwd = {str(s.get("cwd")): s for s in sessions if s.get("cwd")}
    cmap = tree if tree is not None else children_map()

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
        elif isinstance(pane_pid, int):
            for d in descendants(pane_pid, limit=50, tree=cmap):
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
    # Require positive title signal when multiple sessions possible
    if best_score == 0 and len(unique_sessions) > 1:
        return None
    if best_score == 0 and len(unique_sessions) == 1:
        return _link_from_session(best_s, match=best_match)
    if best_score > 0:
        # require clear winner when multi-session
        if len(unique_sessions) > 1:
            second = scored[1][0] if len(scored) > 1 else -1
            if best_score < second + 5 and best_score == second:
                return None
        return _link_from_session(best_s, match=f"{best_match}+title")
    return None


def find_agent_link(
    window_pid: int | None,
    *,
    app: str | None = None,
    title: str | None = None,
    sessions: list[dict[str, Any]] | None = None,
    tree: dict[int, list[int]] | None = None,
) -> dict[str, Any] | None:
    """
    Link a focused window to a Grok/Claude session.

    1) Process tree under the window pid
    2) Terminal (ghostty) + title ends with '- grok' → tmux pane + title≈project
    """
    sess = sessions if sessions is not None else load_grok_sessions()
    cmap = tree if tree is not None else children_map()

    if window_pid is not None:
        hit = _find_via_process_tree(window_pid, sess, tree=cmap)
        if hit is not None:
            return hit

    app_l = (app or "").lower()
    title_s = (title or "").strip()
    # Strict terminal gate — no "claude" substring on Chrome titles
    if app_l not in _TERMINAL_APPS:
        return None
    if not title_s.lower().endswith("- grok") and " - grok" not in title_s.lower():
        return None
    return _find_via_tmux_title(title_s, sess, tree=cmap)
