"""IdleWatch teardown hygiene."""

from __future__ import annotations

from roxabi_sense.collectors.idle_watch import IdleWatch


def test_stop_without_start_is_safe() -> None:
    w = IdleWatch(lambda _m: None)
    w.stop()
    assert w.ready is False
    assert w.running is False


def test_callback_error_logged(monkeypatch, capsys) -> None:
    def boom(_msg: dict) -> None:
        raise RuntimeError("cb-fail")

    w = IdleWatch(boom)

    # inject a synthetic line via _read_loop internals after fake proc
    class FakeStdout:
        def __iter__(self):
            yield '{"type":"ready","source":"wayland-idle"}\n'

        def close(self) -> None:
            pass

    class FakeProc:
        stdout = FakeStdout()

        def poll(self):
            return 0

        def terminate(self):
            pass

        def kill(self):
            pass

        def wait(self, timeout=None):
            return 0

    w._proc = FakeProc()  # type: ignore[assignment]
    w._generation = 1
    w._read_loop(1)
    err = capsys.readouterr().out
    assert "callback error" in err
    assert "cb-fail" in err
