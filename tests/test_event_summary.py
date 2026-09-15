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


def test_summarize_herdr_snapshot() -> None:
    line = summarize_event(
        "herdr_snapshot",
        {
            "count": 2,
            "panes": [
                {
                    "pane_id": "p1",
                    "cwd": "/tmp/omp-sense",
                    "agent": "omp",
                    "status": "working",
                    "focused": True,
                    "session_id": "abc123",
                    "terminal_title": "π > rewrite the agent prompt",
                    "terminal_title_stripped": "rewrite the agent prompt",
                },
                {
                    "pane_id": "p2",
                    "cwd": "/tmp/other",
                    "agent": "grok",
                    "status": "idle",
                    "focused": False,
                    "session_id": "def456",
                },
            ],
        },
    )
    assert "n=2" in line
    assert "omp@/tmp/omp-sense" in line
    assert "omp-sense" in line
    assert "prompt" not in line
    assert "π" not in line
