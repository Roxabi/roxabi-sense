"""Focus integration preserves native identity while Tern evidence changes."""
from roxabi_sense.collectors.focus.collector import FocusCollector
from roxabi_sense.collectors.focus.protocol import FocusWindow
from roxabi_sense.store import Store


def test_tern_focus_context_replaces_and_clears_without_generic_linking(tmp_path, monkeypatch):
    import roxabi_sense.collectors.focus.collector as module

    def forbidden(*args, **kwargs):
        raise AssertionError("Tern must not consult generic agent histories")

    monkeypatch.setattr(module, "resolve_app_name", lambda app, pid: app)
    monkeypatch.setattr(module, "find_agent_link", forbidden)
    monkeypatch.setattr(module, "children_map", forbidden)
    monkeypatch.setattr(module, "list_mux_agent_panes", forbidden)
    current = {"source": "tern", "ref": "pane-a", "repo_ref": "repo-a",
               "proof_ref": "proof-a", "agent": {"agent": "omp", "match": "tern_foreground"}}
    monkeypatch.setattr(module, "tern_context", lambda *args: current.copy() if current else None)
    collector = FocusCollector(sessions_loader=forbidden)
    windows = [FocusWindow("so.stencil.tern", "[sense:t1:abcdefghijklmnop] shell", True,
                           window_id="epoch:window")]
    with Store(tmp_path / "sense.db") as store:
        collector.apply(store, windows, mode="full", source="cosmic")
        first = store.last_by_kind("focus")
        assert first is not None
        assert first.payload["title"] == "shell"
        assert "sense:t1" not in str(first.payload)
        assert first.payload["context"]["ref"] == "pane-a"
        assert first.payload["agent"]["match"] == "tern_foreground"
        collector.apply(store, windows, mode="focus", source="cosmic")
        unchanged = store.last_by_kind("focus")
        assert unchanged is not None
        assert unchanged.id == first.id
        current["ref"] = "pane-b"
        collector.apply(store, windows, mode="focus", source="cosmic")
        second = store.last_by_kind("focus")
        assert second is not None
        assert second.id > first.id and second.payload["context"]["ref"] == "pane-b"
        current.clear()
        collector.apply(store, windows, mode="focus", source="cosmic")
        cleared = store.last_by_kind("focus")
        assert cleared is not None
        assert cleared.id > second.id
        assert "context" not in cleared.payload and "agent" not in cleared.payload
        assert cleared.payload["window_id"] == "epoch:window"
