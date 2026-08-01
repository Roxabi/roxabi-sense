"""summarize_event shared by CLI day / future MCP."""

from __future__ import annotations

from roxabi_sense.report import summarize_event


def test_summarize_focus_with_agent() -> None:
    line = summarize_event(
        "focus",
        {
            "app": "ghostty",
            "title": "sense",
            "agent": {"agent": "grok", "cwd": "/tmp/x"},
        },
    )
    assert "ghostty" in line
    assert "grok" in line
    assert "/tmp/x" in line


def test_summarize_idle() -> None:
    line = summarize_event(
        "idle",
        {"idle": True, "source": "wayland-idle", "idle_since": "2026-08-01T12:00:00Z"},
    )
    assert "idle=True" in line
    assert "wayland-idle" in line


def test_summarize_unknown_kind_falls_back_to_json() -> None:
    line = summarize_event("noise", {"x": 1})
    assert "x" in line
