"""Join window focus → agent session (tmux/herdr pane + process tree)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from roxabi_sense.util.mux import list_mux_agent_panes, list_tmux_agent_panes
from roxabi_sense.util.proc import children_map, descendants, read_comm, read_cwd
from roxabi_sense.util.session_registry import load_all_sessions, load_grok_sessions
from roxabi_sense.util.titles import is_generic_app_title, score_pane_title

_AGENT_COMMS = frozenset({"grok", "claude", "omp"})
_TERMINAL_APPS = frozenset({"ghostty", "unnamed", "herdr"})
_HERDR_FOCUS_APPS = frozenset({"ghostty", "herdr"})
_PANE_TITLE_EARLY_MIN = 90
_UNIQUE_MARGIN = 5
_TITLE_CWD_CAP = 4
_CWD_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*")

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
            if scwd and cwd and str(scwd) == cwd:
                return _link_from_session(s, match="cwd", pid=pid)
            if scwd and cwd:
                try:
                    if Path(str(scwd)).resolve() == Path(cwd):
                        return _link_from_session(s, match="cwd", pid=pid)
                except OSError:
                    pass
        if bare_comm is None:
            bare_comm = {
                "agent": comm,
                "session_id": None,
                "cwd": cwd,
                "pid": pid,
                "match": "comm",
            }
    return bare_comm


def _unique_mux_pane(panes: list[dict[str, Any]], *, mux_name: str) -> dict[str, Any] | None:
    if mux_name == "herdr":
        hits = [p for p in panes if p.get("mux") == "herdr" and p.get("focused")]
    else:
        hits = [p for p in panes if p.get("mux") != "herdr" and p.get("attached")]
    if len(hits) == 1:
        return hits[0]
    return None


def _focused_match(pane: dict[str, Any]) -> str:
    if str(pane.get("mux") or "") == "herdr":
        return "herdr_focused"
    return "mux_focused"


def _score_title_cwd(title: str, cwd: str) -> int:
    if not title or not cwd:
        return 0
    base = Path(cwd).name.lower()
    if len(base) < 3:
        return 0
    tokens = set(_CWD_TOKEN_RE.findall(title.lower()))
    if base in tokens:
        return _TITLE_CWD_CAP
    parts = [p for p in re.split(r"[-_]+", base) if p]
    if len(parts) >= 2 and all(p in tokens for p in parts):
        return _TITLE_CWD_CAP
    return 0


def _resolve_pane_session(
    pane: dict[str, Any],
    *,
    by_pid: dict[int, dict[str, Any]],
    by_cwd: dict[str, dict[str, Any]],
    tree: dict[int, list[int]],
) -> tuple[dict[str, Any], str] | None:
    path = str(pane.get("path") or "")
    pane_pid = pane.get("pane_pid")
    if isinstance(pane_pid, int):
        if pane_pid in by_pid:
            return by_pid[pane_pid], "tmux_pane_pid"
        for child in descendants(pane_pid, tree=tree):
            if child in by_pid:
                return by_pid[child], "tmux_child_pid"
    if path:
        if path in by_cwd:
            return by_cwd[path], "tmux_cwd"
        try:
            resolved = str(Path(path).resolve())
        except OSError:
            resolved = ""
        if resolved and resolved in by_cwd:
            return by_cwd[resolved], "tmux_cwd"
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
    """Disambiguate multi-pane Ghostty via AT-SPI title vs mux pane_title."""
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
    if best_sc == 100 and second_sc < 100:
        return _link_from_pane(
            best_pane, sessions, tree=tree, match="tmux_pane_title"
        )
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
    app: str = "",
    panes: list[dict[str, Any]] | None = None,
    tree: dict[int, list[int]] | None = None,
) -> dict[str, Any] | None:
    """Match focused terminal to session via mux pane path/pid (+ title break ties)."""
    agent_panes = panes if panes is not None else list_mux_agent_panes()
    if not agent_panes:
        return None

    cmap = tree if tree is not None else children_map()

    if not is_generic_app_title(title):
        hit = _find_via_pane_title(title, sessions, panes=agent_panes, tree=cmap)
        if hit is not None:
            return hit
    else:
        mux_name = "herdr" if app in _HERDR_FOCUS_APPS else "tmux"
        focused = _unique_mux_pane(agent_panes, mux_name=mux_name)
        if focused is not None:
            return _link_from_pane(
                focused, sessions, tree=cmap, match=_focused_match(focused)
            )

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
        base = 20 if match in {"tmux_child_pid", "tmux_pane_pid"} else 15
        herdr = str(pane.get("mux") or "") == "herdr"
        active = pane.get("focused") if herdr else pane.get("attached")
        scored.append((base + title_score, 1 if active else 0, s, match))

    if not scored:
        return None
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    best_score, _att, best_s, best_match = scored[0]
    unique = {str(s.get("session_id")) for _, _, s, _ in scored}
    if len(unique) == 1:
        return _link_from_session(best_s, match=best_match)
    second = scored[1][0] if len(scored) > 1 else -1
    if best_score >= second + _UNIQUE_MARGIN:
        return _link_from_session(best_s, match=f"{best_match}+title")
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
    """Link a focused window to a grok/claude/omp session.

    1) Process tree under window pid
    2) mux panes (tmux grok|claude|omp + herdr agents):
       a) pane_title vs focus title
       b) unique attached/focused pane (generic Ghostty/herdr titles only)
       c) pane_pid descendants + path==cwd (+ basename title score)
    """
    sess = sessions if sessions is not None else load_all_sessions()
    cmap = tree if tree is not None else children_map()

    if window_pid is not None:
        hit = _find_via_process_tree(window_pid, sess, tree=cmap)
        if hit is not None and hit.get("session_id"):
            return hit

    app_l = (app or "").lower()
    title_s = (title or "").strip()
    looks_agent = (
        app_l in _TERMINAL_APPS
        or " - grok" in title_s.lower()
        or title_s.lower().endswith("- grok")
        or " - claude" in title_s.lower()
        or "claude" in app_l
        or " - omp" in title_s.lower()
        or title_s.lower().endswith("- omp")
    )
    if looks_agent or app_l in _TERMINAL_APPS:
        agent_panes = panes if panes is not None else list_mux_agent_panes()
        tmux_hit = _find_via_tmux(title_s, sess, app=app_l, panes=agent_panes, tree=cmap)
        if tmux_hit is not None:
            return tmux_hit

    if window_pid is not None:
        return _find_via_process_tree(window_pid, sess, tree=cmap)
    return None
