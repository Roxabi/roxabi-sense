"""Join window focus → Grok/Claude session (tmux pane + process tree)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from roxabi_sense.util.proc import children_map, descendants, read_comm, read_cwd
from roxabi_sense.util.session_registry import load_all_sessions, load_grok_sessions
from roxabi_sense.util.titles import score_pane_title

_AGENT_COMMS = frozenset({"grok", "claude"})
_TERMINAL_APPS = frozenset({"ghostty", "unnamed"})
_TMUX = next(
    (p for p in ("/usr/bin/tmux", "/usr/local/bin/tmux") if Path(p).is_file()),
    None,
)
# Early-return pane_title path only at this score or above (exact/prefix).
_PANE_TITLE_EARLY_MIN = 90

# Re-export for callers that imported load_grok_sessions from here
__all__ = [
    "find_agent_link",
    "list_tmux_agent_panes",
    "load_grok_sessions",
    "score_pane_title",
]


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
    agent = str(s.get("agent") or "grok")
    return {
        "agent": agent,
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
    """Panes whose current command is grok/claude.

    Includes ``pane_title`` (often equals Ghostty/AT-SPI window title) so multi-pane
    layouts can disambiguate without relying on repo basename in the title.
    """
    if not _TMUX:
        return []
    # pane_title last: may contain spaces; we still use tab-split (titles rarely have tabs)
    fmt = (
        "#{pane_pid}\t#{pane_current_command}\t#{pane_current_path}\t"
        "#{session_attached}\t#{pane_title}"
    )
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
        pane_title = parts[4] if len(parts) > 4 else ""
        # re-join if title itself contained tabs (defensive)
        if len(parts) > 5:
            pane_title = "\t".join(parts[4:])
        if cmd not in _AGENT_COMMS:
            continue
        panes.append(
            {
                "pane_pid": int(pid_s) if pid_s.isdigit() else None,
                "command": cmd,
                "path": path,
                "attached": attached == "1",
                "pane_title": pane_title,
            }
        )
    return panes


def _score_title_cwd(title: str, cwd: str) -> int:
    if not title or not cwd:
        return 0
    base = Path(cwd).name
    if not base or len(base) < 3:
        return 0
    t = f" {title.lower()} "
    b = base.lower()
    score = 0
    if re.search(rf"(?<![a-z0-9]){re.escape(b)}(?![a-z0-9])", t):
        score += 10
    spaced = b.replace("-", " ").replace("_", " ")
    if spaced != b and spaced in title.lower():
        score += 8
    for tok in re.split(r"[-_]", b):
        if len(tok) >= 5 and re.search(rf"(?<![a-z0-9]){re.escape(tok)}(?![a-z0-9])", t):
            score += 3
    return score


def _resolve_pane_session(
    pane: dict[str, Any],
    *,
    by_pid: dict[int, dict[str, Any]],
    by_cwd: dict[str, dict[str, Any]],
    tree: dict[int, list[int]],
) -> tuple[dict[str, Any], str] | None:
    path = str(pane.get("path") or "")
    pane_pid = pane.get("pane_pid")
    # 1) session.pid is descendant of pane shell (or equals pane)
    if isinstance(pane_pid, int):
        if pane_pid in by_pid:
            return by_pid[pane_pid], "tmux_pane_pid"
        for d in descendants(pane_pid, limit=80, tree=tree):
            if d in by_pid:
                return by_pid[d], "tmux_child_pid"
    # 2) pane path == session cwd
    if path:
        if path in by_cwd:
            return by_cwd[path], "tmux_path"
        try:
            resolved = str(Path(path).resolve())
            if resolved in by_cwd:
                return by_cwd[resolved], "tmux_path"
        except OSError:
            pass
    return None


def _link_from_pane(
    pane: dict[str, Any],
    sessions: list[dict[str, Any]],
    *,
    tree: dict[int, list[int]],
    match: str,
) -> dict[str, Any]:
    """Build agent link from a concrete pane (session registry if possible)."""
    by_pid = _session_by_pid(sessions)
    by_cwd: dict[str, dict[str, Any]] = {}
    for s in sessions:
        cwd = s.get("cwd")
        if not cwd:
            continue
        by_cwd[str(cwd)] = s
        try:
            by_cwd[str(Path(str(cwd)).resolve())] = s
        except OSError:
            pass
    resolved = _resolve_pane_session(pane, by_pid=by_pid, by_cwd=by_cwd, tree=tree)
    if resolved is not None:
        s, struct_match = resolved
        return _link_from_session(s, match=f"{match}+{struct_match}")
    # Session registry miss: still emit pane path (cwd authority for time_by_repo)
    path = str(pane.get("path") or "") or None
    cmd = str(pane.get("command") or "grok")
    return {
        "agent": cmd if cmd in _AGENT_COMMS else "grok",
        "session_id": None,
        "cwd": path,
        "pid": pane.get("pane_pid"),
        "match": match,
    }


def _find_via_pane_title(
    title: str,
    sessions: list[dict[str, Any]],
    *,
    panes: list[dict[str, Any]],
    tree: dict[int, list[int]],
) -> dict[str, Any] | None:
    """Disambiguate multi-pane Ghostty via AT-SPI title ↔ tmux pane_title."""
    if not title or not panes:
        return None
    scored: list[tuple[int, dict[str, Any]]] = []
    for pane in panes:
        pt = str(pane.get("pane_title") or "")
        sc = score_pane_title(title, pt)
        if sc > 0:
            scored.append((sc, pane))
    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    best_sc, best_pane = scored[0]
    second_sc = scored[1][0] if len(scored) > 1 else 0
    # Identity class: unique exact always wins (even if a related pane scores 90).
    if best_sc == 100 and second_sc < 100:
        return _link_from_pane(
            best_pane, sessions, tree=tree, match="tmux_pane_title"
        )
    # Prefix tier: unique best at ≥90 (strictly above #2). Weak 70/80 never preempt
    # structural path/pid + basename scoring.
    if (
        best_sc >= _PANE_TITLE_EARLY_MIN
        and best_sc > second_sc
        and best_sc < 100
    ):
        return _link_from_pane(
            best_pane, sessions, tree=tree, match="tmux_pane_title"
        )
    return None


def _find_via_tmux(
    title: str,
    sessions: list[dict[str, Any]],
    *,
    panes: list[dict[str, Any]] | None = None,
    tree: dict[int, list[int]] | None = None,
) -> dict[str, Any] | None:
    """Match focused terminal to session via tmux pane path/pid (+ title break ties)."""
    agent_panes = panes if panes is not None else list_tmux_agent_panes()
    if not agent_panes:
        return None

    cmap = tree if tree is not None else children_map()

    # 0) pane_title ↔ window title (works with 10+ parallel grok panes)
    hit = _find_via_pane_title(title, sessions, panes=agent_panes, tree=cmap)
    if hit is not None:
        return hit

    if not sessions:
        return None

    by_pid = _session_by_pid(sessions)
    by_cwd: dict[str, dict[str, Any]] = {}
    for s in sessions:
        cwd = s.get("cwd")
        if not cwd:
            continue
        by_cwd[str(cwd)] = s
        try:
            by_cwd[str(Path(str(cwd)).resolve())] = s
        except OSError:
            pass

    scored: list[tuple[int, int, dict[str, Any], str]] = []
    for pane in agent_panes:
        resolved = _resolve_pane_session(pane, by_pid=by_pid, by_cwd=by_cwd, tree=cmap)
        if resolved is None:
            continue
        s, match = resolved
        title_score = _score_title_cwd(title, str(s.get("cwd") or pane.get("path") or ""))
        # Strong structural match: path/pid without title still counts high base
        base = 20 if match in {"tmux_child_pid", "tmux_pane_pid"} else 15
        attached_bonus = 1 if pane.get("attached") else 0
        scored.append((base + title_score, attached_bonus, s, match))

    if not scored:
        return None
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    best_score, _att, best_s, best_match = scored[0]
    unique = {str(s.get("session_id")) for _, _, s, _ in scored}
    if len(unique) == 1:
        return _link_from_session(best_s, match=best_match)
    # multi-session: need title to break ties
    second = scored[1][0] if len(scored) > 1 else -1
    if best_score >= second + 5:
        return _link_from_session(best_s, match=f"{best_match}+title")
    # path unique among top scores?
    return None


def find_agent_link(
    window_pid: int | None,
    *,
    app: str | None = None,
    title: str | None = None,
    sessions: list[dict[str, Any]] | None = None,
    tree: dict[int, list[int]] | None = None,
    panes: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """
    Link a focused window to a Grok/Claude session.

    1) Process tree under window pid (rare for Ghostty→tmux layout)
    2) tmux panes (command=grok|claude):
       a) ``pane_title`` ↔ focus title (multi-pane disambiguation)
       b) pane_pid descendants + path==cwd (+ basename title score)

    Pass ``panes`` (and ``sessions`` / ``tree``) from a batch enrich so
    ``list_tmux_agent_panes`` runs once per tick, not once per window.
    """
    sess = sessions if sessions is not None else load_all_sessions()
    cmap = tree if tree is not None else children_map()

    if window_pid is not None:
        hit = _find_via_process_tree(window_pid, sess, tree=cmap)
        if hit is not None and hit.get("session_id"):
            return hit
        # bare comm without session_id: keep as weak fallback after tmux

    app_l = (app or "").lower()
    title_s = (title or "").strip()
    looks_agent = (
        app_l in _TERMINAL_APPS
        or " - grok" in title_s.lower()
        or title_s.lower().endswith("- grok")
        or " - claude" in title_s.lower()
        or "claude" in app_l
    )
    if looks_agent or app_l in _TERMINAL_APPS:
        tmux_hit = _find_via_tmux(title_s, sess, panes=panes, tree=cmap)
        if tmux_hit is not None:
            return tmux_hit

    if window_pid is not None:
        return _find_via_process_tree(window_pid, sess, tree=cmap)
    return None
