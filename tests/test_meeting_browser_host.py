"""Native wire tests use real subprocesses, pipes and a temporary SQLite store."""

import json
import struct
import subprocess
import sys
import uuid

from roxabi_sense.store import Store
from roxabi_sense.store.meeting_protocol import SCOPE, validate_snapshot, validate_sources

EXTENSION = "a" * 32
RUN_HOST = """
import sys
from pathlib import Path
from roxabi_sense.config import SenseConfig
from roxabi_sense.collectors.meeting_browser.host import run_host
cfg = SenseConfig(db_path=Path(sys.argv[1]), meeting_browser=sys.argv[5] == 'yes')
raise SystemExit(run_host(cfg, sys.argv[2], sys.argv[3], sys.argv[4]))
"""


def frame(data):
    raw = json.dumps(data).encode()
    return struct.pack("=I", len(raw)) + raw


def setup_store(path):
    source = str(uuid.uuid4())
    registry = {"v": 1, "scope": SCOPE, "sources": [source]}
    with Store(path) as store:
        store.set_meta("meeting_browser_sources", json.dumps(registry))
        store.set_meta("meeting_browser_bindings", json.dumps({source: EXTENSION}))
    assert validate_sources(registry)
    return source


def execute(path, source, data, *, origin=None, enabled=True):
    return subprocess.run(
        [sys.executable, "-c", RUN_HOST, str(path), source, EXTENSION,
         origin or f"chrome-extension://{EXTENSION}/", "yes" if enabled else "no"],
        input=data, capture_output=True, timeout=5, check=False,
    )


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


def test_sources_require_exact_shape_and_unique_uuids():
    source = str(uuid.uuid4())
    assert validate_sources({"v": 1, "scope": SCOPE, "sources": [source]})
    assert not validate_sources({"v": 1, "scope": SCOPE, "sources": [source, source]})
    assert not validate_sources({"v": True, "scope": SCOPE, "sources": []})
