"""Pure, fail-closed native window to Tern route resolution."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from roxabi_sense.collectors.focus.protocol import FocusWindow
from roxabi_sense.collectors.tern.metadata import opaque, selected_semantic
from roxabi_sense.util.time import parse_ts

_MARKER = re.compile(r"^\[sense:t1:([A-Za-z0-9_-]{16})\](?: |$)")


def split_marker(title: str) -> tuple[str | None, str]:
    match = _MARKER.match(title)
    if match is None:
        return None, title
    return match.group(1), title[match.end():]


def resolve_context(
    native_window: FocusWindow,
    inventory: list[FocusWindow],
    snapshot: dict[str, Any],
    observation: dict[str, Any],
    registrations: list[dict[str, Any]],
    now: datetime,
    boottime: float,
    boot_id: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Resolve only a unique marker, registration, route, pane and live proof.

    Inventory must be the completed native FULL inventory for the same epoch.
    Caller verifies native coverage and snapshot ID before supplying it.
    """
    if native_window.app != "so.stencil.tern" or not native_window.window_id:
        return None, "not-native-tern"
    marker, _ = split_marker(native_window.title)
    if marker is None:
        return None, "marker-missing"
    matching_windows = [w for w in inventory if split_marker(w.title)[0] == marker]
    if (len(matching_windows) != 1
            or matching_windows[0].window_id != native_window.window_id):
        return None, "marker-ambiguous"
    live = [r for r in registrations if r.get("boot_id") == boot_id
            and isinstance(r.get("expires_boottime"), (int, float))
            and not isinstance(r.get("expires_boottime"), bool)
            and r["expires_boottime"] > boottime]
    records = [r for r in live if r.get("token") == marker]
    if len(records) != 1:
        return None, "registration-unknown"
    route = records[0]
    if not isinstance(route.get("endpoint_ref"), str):
        return None, "registration-unknown"
    if sum(all(r.get(k) == route.get(k) for k in ("endpoint_ref", "window_key", "conn", "since"))
           for r in live) != 1:
        return None, "registration-ambiguous"
    windows = snapshot.get("windows")
    if not isinstance(windows, list) or snapshot.get("state") != "observed":
        return None, "source-unknown"
    matches = [w for w in windows if isinstance(w, dict)
               and all(w.get(k) == route.get(k)
                       for k in ("endpoint_ref", "window_key", "conn", "since"))]
    if len(matches) != 1:
        return None, "route-unknown"
    window = matches[0]
    panes = window.get("panes")
    selected = window.get("shown_pane")
    if not isinstance(panes, list) or not isinstance(selected, str):
        return None, "selection-unknown"
    matches = [p for p in panes if isinstance(p, dict) and p.get("pane_ref") == selected
               and p.get("selected") is True]
    if len(matches) != 1:
        return None, "selection-ambiguous"
    pane = matches[0]
    if (observation.get("healthy") is not True
            or observation.get("run_ref") != snapshot.get("run_ref")):
        return None, "observation-unknown"
    scopes = observation.get("scopes")
    scope = opaque("selected-scope", [route.get("endpoint_ref"), route.get("window_key"),
                                      route.get("conn"), route.get("since")])
    proof = scopes.get(scope) if isinstance(scopes, dict) else None
    semantic = selected_semantic(pane)
    if not isinstance(proof, dict) or proof.get("semantic_ref") != opaque("selected", semantic):
        return None, "proof-unknown"
    try:
        if parse_ts(proof["fresh_until"]) < now or not isinstance(proof["proof_ref"], str):
            return None, "proof-expired"
    except (KeyError, ValueError, TypeError):
        return None, "proof-unknown"
    context = {"source": "tern", "ref": opaque("context", pane["pane_ref"],
               pane.get("session_context_ref"), pane.get("repo_ref")),
               "repo_ref": pane.get("repo_ref"), "cwd": pane.get("cwd"),
               "proof_ref": proof["proof_ref"]}
    process = scopes.get(opaque("process-scope", pane["pane_ref"])) if isinstance(
        scopes, dict
    ) else None
    if pane.get("omp_foreground") is True and isinstance(process, dict):
        try:
            if (process.get("semantic_ref") == opaque("process", pane["pane_ref"], True)
                    and isinstance(process.get("proof_ref"), str)
                    and parse_ts(process["fresh_until"]) >= now):
                context["agent"] = {"agent": "omp", "match": "tern_foreground"}
                context["agent_proof_ref"] = process["proof_ref"]
        except (KeyError, ValueError, TypeError):
            pass
    return context, None
