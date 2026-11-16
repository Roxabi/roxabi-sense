"""Single-flight Tern acquisition, with publication on the daemon thread."""
from __future__ import annotations

import logging
import threading
from typing import Any

from roxabi_sense.collectors.tern.bridge import boot_identity
from roxabi_sense.collectors.tern.metadata import endpoint_ref

_LOG = logging.getLogger(__name__)
_INTERVAL = 5.0
_BUDGET = 8.0


class TernWorker:
    def __init__(self, collector: Any) -> None:
        self.collector = collector
        self._cancel = threading.Event()
        self._generation = 0
        self._sequence = 0
        self._consumed = 0
        self._thread: threading.Thread | None = None
        self._result: tuple[Any, ...] | None = None
        self._next = 0.0
        self._running = False

    def start(self) -> None:
        if self._running:
            raise RuntimeError("Tern worker already started")
        self._running = True
        self._generation += 1
        self._cancel.clear()
        self._submit()

    def _submit(self) -> None:
        boot, now = boot_identity()
        self._sequence += 1
        generation, sequence = self._generation, self._sequence
        endpoint = self.collector.endpoint
        deadline = now + _BUDGET
        self._next = now + _INTERVAL

        def acquire() -> None:
            try:
                sample = self.collector.collect(self._cancel)
            except Exception:
                _LOG.exception("Tern collection failed")
                sample = None
            self._result = (generation, sequence, boot, endpoint, deadline, sample)

        self._thread = threading.Thread(target=acquire, name="tern-metadata")
        self._thread.start()

    def poll(self, store: Any) -> int:
        if not self._running:
            return 0
        wrote = self.collector.expire(store) or 0
        thread = self._thread
        if thread is not None and not thread.is_alive():
            thread.join()
            self._thread = None
            result, self._result = self._result, None
            if result is not None:
                generation, sequence, boot, endpoint, deadline, sample = result
                current_boot, now = boot_identity()
                if (generation == self._generation and sequence > self._consumed
                        and boot == current_boot and endpoint == self.collector.endpoint
                        and endpoint == endpoint_ref() and now < deadline
                        and not self._cancel.is_set()):
                    self._consumed = sequence
                    wrote += self.collector.publish(store, sample) or 0
        if self._thread is None and boot_identity()[1] >= self._next:
            self._submit()
        return wrote

    def close(self, store: Any) -> None:
        self._running = False
        self._generation += 1
        self._cancel.set()
        try:
            if self._thread is not None:
                self._thread.join()
                self._thread = None
            self._result = None
        finally:
            self.collector.shutdown(store)
