"""tmux session / window / pane snapshot (Ghostty → tmux → grok path)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from roxabi_sense.store import Store
from roxabi_sense.util import mux

KIND = "tmux"


class TmuxSessionsCollector:
    name = "tmux"

    def __init__(
        self,
        *,
        list_panes: Callable[[], list[dict[str, Any]]] | None = None,
    ) -> None:
        self._last: str | None = None
        self._list_panes_fn = list_panes

    def tick(self, store: Store) -> int:
        if self._list_panes_fn is None and not mux.tmux_bin():
            return 0
        panes = self._list_panes()
        # Fingerprint structure only — pane_title churns with Thinking/Running.
        fingerprint = json.dumps(
            [
                {
                    k: p.get(k)
                    for k in (
                        "session",
                        "window",
                        "window_name",
                        "command",
                        "path",
                        "attached",
                    )
                }
                for p in panes
            ],
            sort_keys=True,
        )
        if fingerprint == self._last:
            return 0
        self._last = fingerprint
        stored = [{k: v for k, v in p.items() if k != "pane_title"} for p in panes]
        store.append(KIND + "_snapshot", {"panes": stored, "count": len(panes)})
        return 1

    def _list_panes(self) -> list[dict[str, Any]]:
        if self._list_panes_fn is not None:
            return self._list_panes_fn()
        return mux.list_tmux_panes()
