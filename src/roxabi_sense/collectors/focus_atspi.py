"""Back-compat entry for focus collector (implementation in collectors.focus)."""

from __future__ import annotations

from roxabi_sense.collectors.focus.collector import FocusCollector, WindowInfo
from roxabi_sense.collectors.focus.protocol import FocusWindow, raw_dicts_to_windows

# Re-export enrich helpers under this module so existing tests can monkeypatch
# `roxabi_sense.collectors.focus_atspi.resolve_app_name` etc.
from roxabi_sense.util.agent_link import find_agent_link, list_tmux_agent_panes
from roxabi_sense.util.proc import children_map, resolve_app_name
from roxabi_sense.util.session_registry import load_all_sessions
from roxabi_sense.util.titles import normalize_title, sanitize_display

FocusAtspiCollector = FocusCollector

__all__ = [
    "FocusAtspiCollector",
    "FocusCollector",
    "FocusWindow",
    "WindowInfo",
    "children_map",
    "find_agent_link",
    "list_tmux_agent_panes",
    "load_all_sessions",
    "normalize_title",
    "raw_dicts_to_windows",
    "resolve_app_name",
    "sanitize_display",
]
