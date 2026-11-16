"""Native wire tests use real subprocesses, pipes and a temporary SQLite store."""

import json
import struct
import subprocess
import sys
import time
import uuid

from roxabi_sense.collectors.meeting_browser.source_lock import SourceLock
from roxabi_sense.store import Store
from roxabi_sense.store.meeting_protocol import (
    CONFLICTS_META,
    SCOPE,
    validate_snapshot,
    validate_sources,
)

EXTENSION = "a" * 32
RUN_HOST = """
import sys
from pathlib import Path
from roxabi_sense.config import SenseConfig
from roxabi_sense.collectors.meeting_browser.host import run_host
cfg = SenseConfig(db_path=Path(sys.argv[1]), meeting_browser=sys.argv[5] == 'yes')
raise SystemExit(run_host(cfg, sys.argv[2], sys.argv[3], sys.argv[4], Path(sys.argv[6])))
"""


def frame(data):
    raw = json.dumps(data).encode()
    return struct.pack("=I", len(raw)) + raw


def setup_store(path):
    source = str(uuid.uuid4())
    path.with_suffix(".toml").write_text(
        f'[daemon]\ndb_path = "{path}"\n[collectors]\nmeeting_browser = true\n'
    )
    registry = {"v": 1, "scope": SCOPE, "sources": [source]}
    with Store(path) as store:
        store.set_meta("meeting_browser_sources", json.dumps(registry))
        store.set_meta("meeting_browser_bindings", json.dumps({source: EXTENSION}))
    assert validate_sources(registry)
    return source


def execute(path, source, data, *, origin=None, enabled=True):
    return subprocess.run(
        [sys.executable, "-c", RUN_HOST, str(path), source, EXTENSION,
         origin or f"chrome-extension://{EXTENSION}/", "yes" if enabled else "no",
         str(path.with_suffix(".toml"))],
        input=data, capture_output=True, timeout=5, check=False,
    )


def start(path, source):
    return subprocess.Popen(
        [sys.executable, "-c", RUN_HOST, str(path), source, EXTENSION,
         f"chrome-extension://{EXTENSION}/", "yes", str(path.with_suffix(".toml"))],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def wait_snapshot(path):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with Store(path) as store:
            event = store.last_by_kind("meeting_snapshot")
        if event is not None:
            return event
        time.sleep(0.02)
    raise AssertionError("snapshot not received")


def test_native_snapshot_is_normalized_and_registered(tmp_path):
    path = tmp_path / "sense.db"
    source = setup_store(path)
    tab = str(uuid.uuid4())
    payload = {"v": 1, "seq": 0, "complete": True, "tabs": [
        {"tab_id": tab, "state": "unknown", "basis": "no_response"}
    ]}
    result = execute(path, source, frame(payload))
    assert result.returncode == 0 and result.stdout == b""
    with Store(path) as store:
        event = store.last_by_kind("meeting_snapshot")
    assert event is not None and validate_snapshot(event.payload)
    assert event.payload["source_id"] == source
    assert event.payload["tabs"] == payload["tabs"]


def test_rejects_origin_opt_out_unregistered_and_malicious_frames(tmp_path):
    path = tmp_path / "sense.db"
    source = setup_store(path)
    good = {"v": 1, "seq": 0, "complete": True, "tabs": []}
    assert execute(path, source, frame(good), origin="https://meet.google.com/").returncode != 0
    assert execute(path, source, frame(good), enabled=False).returncode != 0
    assert execute(path, str(uuid.uuid4()), frame(good)).returncode != 0
    for data in [
        frame({**good, "url": "https://meet.google.com/secret"}),
        frame({**good, "seq": 2}),
        frame({**good, "complete": False, "tabs": [
            {"tab_id": str(uuid.uuid4()), "state": "joined", "basis": "leave_control"}
        ]}),
        struct.pack("=I", 65537),
        struct.pack("=I", 5) + b"{}",
    ]:
        assert execute(path, source, data).returncode != 0
    with Store(path) as store:
        assert store.last_by_kind("meeting_snapshot") is None


def test_incomplete_enumeration_is_unknown(tmp_path):
    path = tmp_path / "sense.db"
    source = setup_store(path)
    payload = {"v": 1, "seq": 0, "complete": False, "tabs": []}
    assert execute(path, source, frame(payload)).returncode == 0
    with Store(path) as store:
        event = store.last_by_kind("meeting_snapshot")
    assert event is not None and event.payload["complete"] is False


def test_source_is_bound_to_one_extension(tmp_path):
    path = tmp_path / "sense.db"
    source = setup_store(path)
    with Store(path) as store:
        store.set_meta("meeting_browser_bindings", json.dumps({source: "b" * 32}))
    payload = {"v": 1, "seq": 0, "complete": True, "tabs": []}
    assert execute(path, source, frame(payload)).returncode != 0


def test_rate_limit_and_restart_epoch(tmp_path):
    path = tmp_path / "sense.db"
    source = setup_store(path)
    first = {"v": 1, "seq": 0, "complete": True, "tabs": []}
    assert execute(path, source, frame(first) + frame({**first, "seq": 1})).returncode != 0
    with Store(path) as store:
        before = store.last_by_kind("meeting_snapshot")
    assert execute(path, source, frame(first)).returncode == 0
    with Store(path) as store:
        after = store.last_by_kind("meeting_snapshot")
    assert before is not None and after is not None
    assert before.payload["epoch"] != after.payload["epoch"]
    assert after.payload["seq"] == 0


def test_concurrent_producer_latches_and_invalidates_until_removed(tmp_path):
    path = tmp_path / "sense.db"
    source = setup_store(path)
    joined = {"v": 1, "seq": 0, "complete": True, "tabs": [
        {"tab_id": str(uuid.uuid4()), "state": "joined", "basis": "leave_control"}
    ]}
    process = start(path, source)
    assert process.stdin is not None
    try:
        process.stdin.write(frame(joined))
        process.stdin.flush()
        assert wait_snapshot(path).payload["complete"] is True
        assert execute(path, source, frame(joined)).returncode != 0
        with Store(path) as store:
            event = store.last_by_kind("meeting_snapshot")
            conflicts = store.get_meta(CONFLICTS_META)
        assert event is not None and event.payload["complete"] is False
        assert conflicts is not None and json.loads(conflicts)["sources"] == [source]
    finally:
        process.stdin.close()
        process.wait(timeout=5)
    assert execute(path, source, frame(joined)).returncode != 0


def test_revoking_config_discards_buffered_snapshot(tmp_path):
    path = tmp_path / "sense.db"
    source = setup_store(path)
    process = start(path, source)
    assert process.stdin is not None
    try:
        payload = {"v": 1, "seq": 0, "complete": True, "tabs": []}
        process.stdin.write(frame(payload))
        process.stdin.flush()
        assert wait_snapshot(path).payload["complete"] is True
        path.with_suffix(".toml").write_text(
            f'[daemon]\ndb_path = "{path}"\n[collectors]\nmeeting_browser = false\n'
        )
        time.sleep(1.1)
        process.stdin.write(frame({**payload, "seq": 1}))
        process.stdin.flush()
        assert process.wait(timeout=5) != 0
        with Store(path) as store:
            event = store.last_by_kind("meeting_snapshot")
        assert event is not None and event.payload["complete"] is False
        assert event.payload["seq"] == 1
    finally:
        process.stdin.close()
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def test_conflict_marker_survives_missing_database_latch(tmp_path):
    path = tmp_path / "sense.db"
    source = setup_store(path)
    owner = SourceLock(path, source)
    script = RUN_HOST.replace(
        "raise SystemExit",
        "import roxabi_sense.collectors.meeting_browser.host as host\n"
        "def failed_latch(*args):\n"
        "    raise OSError('database write failed')\n"
        "host._latch = failed_latch\n"
        "raise SystemExit",
    )
    try:
        assert owner.acquire()
        result = subprocess.run(
            [sys.executable, "-c", script, str(path), source, EXTENSION,
             f"chrome-extension://{EXTENSION}/", "yes", str(path.with_suffix(".toml"))],
            input=b"", capture_output=True, timeout=5, check=False,
        )
        assert result.returncode != 0
    finally:
        owner.close()
    probe = SourceLock(path, source)
    try:
        assert probe.conflicted()
    finally:
        probe.close()
    with Store(path) as store:
        assert store.get_meta(CONFLICTS_META) is None
    payload = {"v": 1, "seq": 0, "complete": True, "tabs": []}
    assert execute(path, source, frame(payload)).returncode != 0
    with Store(path) as store:
        event = store.last_by_kind("meeting_snapshot")
        conflicts = store.get_meta(CONFLICTS_META)
    assert event is not None and event.payload["complete"] is False
    assert conflicts is not None and json.loads(conflicts)["sources"] == [source]


def test_corrupt_conflict_metadata_fails_closed(tmp_path):
    path = tmp_path / "sense.db"
    source = setup_store(path)
    with Store(path) as store:
        store.set_meta(CONFLICTS_META, "{broken")
    payload = {"v": 1, "seq": 0, "complete": True, "tabs": []}
    assert execute(path, source, frame(payload)).returncode != 0
    with Store(path) as store:
        assert store.last_by_kind("meeting_snapshot") is None


def test_unsafe_lock_inode_fails_closed(tmp_path):
    path = tmp_path / "sense.db"
    source = setup_store(path)
    lock = SourceLock(path, source)
    lock.close()
    (path.parent / f".meeting-source-{source}.lock").chmod(0o644)
    payload = {"v": 1, "seq": 0, "complete": True, "tabs": []}
    assert execute(path, source, frame(payload)).returncode != 0
    with Store(path) as store:
        assert store.last_by_kind("meeting_snapshot") is None


def test_sources_require_exact_shape_and_unique_uuids():
    source = str(uuid.uuid4())
    assert validate_sources({"v": 1, "scope": SCOPE, "sources": [source]})
    assert not validate_sources({"v": 1, "scope": SCOPE, "sources": [source, source]})
    assert not validate_sources({"v": True, "scope": SCOPE, "sources": []})
