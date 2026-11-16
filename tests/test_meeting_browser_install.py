"""Behavioral coverage for explicit browser connector ownership."""

import json
import os
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from roxabi_sense.collectors.meeting_browser.install_files import HOST_NAME
from roxabi_sense.collectors.meeting_browser.installer import export_extension, install, remove
from roxabi_sense.config import SenseConfig
from roxabi_sense.store import Store
from roxabi_sense.surfaces.meeting_browser import dispatch

EXTENSION = "a" * 32


def _setup(tmp_path: Path) -> tuple[SenseConfig, Path, Path]:
    profile = tmp_path / "browser" / "Default"
    profile.parent.mkdir(mode=0o700)
    profile.mkdir(mode=0o700)
    return SenseConfig(db_path=tmp_path / "data" / "sense.db"), profile, tmp_path / "config.toml"


def test_export_is_explicit_and_never_overwrites(tmp_path: Path) -> None:
    target = tmp_path / "extension"
    export_extension(target)
    manifest = json.loads((target / "manifest.json").read_text())
    assert manifest["manifest_version"] == 3
    marker = target / "unowned"
    marker.write_text("keep")
    with pytest.raises(FileExistsError):
        export_extension(target)
    assert marker.read_text() == "keep"


def test_registration_allowlist_and_registry_cleanup(tmp_path: Path) -> None:
    cfg, profile, config = _setup(tmp_path)
    source = install(cfg, profile=profile, extension_id=EXTENSION,
                     config_path=config, binary=sys.executable)
    directory = profile.parent / "NativeMessagingHosts"
    registration = json.loads((directory / f"{HOST_NAME}.json").read_text())
    assert registration["allowed_origins"] == [f"chrome-extension://{EXTENSION}/"]
    with Store(cfg.db_path) as store:
        event = store.last_by_kind("meeting_sources")
        bindings = store.get_meta("meeting_browser_bindings")
        assert event is not None and bindings is not None
        assert event.payload["sources"] == [source]
        assert json.loads(bindings) == {source: EXTENSION}
    remove(cfg, profile=profile, extension_id=EXTENSION)
    assert not (directory / f"{HOST_NAME}.json").exists()
    with Store(cfg.db_path) as store:
        event = store.last_by_kind("meeting_sources")
        bindings = store.get_meta("meeting_browser_bindings")
        assert event is not None and bindings is not None
        assert event.payload["sources"] == []
        assert json.loads(bindings) == {}


def test_removal_preserves_other_sources(tmp_path: Path) -> None:
    cfg, profile, config = _setup(tmp_path)
    other = tmp_path / "other-browser" / "Default"
    other.parent.mkdir(mode=0o700)
    other.mkdir(mode=0o700)
    first = install(cfg, profile=profile, extension_id=EXTENSION,
                    config_path=config, binary=sys.executable)
    second = install(cfg, profile=other, extension_id="b" * 32,
                     config_path=config, binary=sys.executable)
    remove(cfg, profile=profile, extension_id=EXTENSION)
    with Store(cfg.db_path) as store:
        event = store.last_by_kind("meeting_sources")
        bindings = store.get_meta("meeting_browser_bindings")
        assert event is not None and bindings is not None
        assert event.payload["sources"] == [second]
        assert json.loads(bindings) == {second: "b" * 32}
    assert first != second


def test_collision_and_symlink_are_not_replaced(tmp_path: Path) -> None:
    cfg, profile, config = _setup(tmp_path)
    directory = profile.parent / "NativeMessagingHosts"
    directory.mkdir(mode=0o700)
    victim = tmp_path / "victim"
    victim.write_text("untouched")
    script = directory / f"{HOST_NAME}.sh"
    script.symlink_to(victim)
    with pytest.raises(FileExistsError):
        install(cfg, profile=profile, extension_id=EXTENSION,
                config_path=config, binary=sys.executable)
    assert victim.read_text() == "untouched"
    assert script.is_symlink()
    script.unlink()
    script.write_text("unowned")
    with pytest.raises(FileExistsError):
        install(cfg, profile=profile, extension_id=EXTENSION,
                config_path=config, binary=sys.executable)
    assert script.read_text() == "unowned"


def test_modified_manifest_cannot_be_removed(tmp_path: Path) -> None:
    cfg, profile, config = _setup(tmp_path)
    source = install(cfg, profile=profile, extension_id=EXTENSION,
                     config_path=config, binary=sys.executable)
    registration = profile.parent / "NativeMessagingHosts" / f"{HOST_NAME}.json"
    registration.write_text("unowned change")
    with pytest.raises(ValueError, match="modified"):
        remove(cfg, profile=profile, extension_id=EXTENSION)
    assert registration.read_text() == "unowned change"
    with Store(cfg.db_path) as store:
        event = store.last_by_kind("meeting_sources")
        assert event is not None
        assert event.payload["sources"] == [source]


@pytest.mark.parametrize("failure", [0, 1, 2, 3])
def test_removal_resumes_after_each_cleanup_phase(
    tmp_path: Path, monkeypatch, failure: int
) -> None:
    cfg, profile, config = _setup(tmp_path)
    other = tmp_path / "other" / "Default"
    other.parent.mkdir(mode=0o700)
    other.mkdir(mode=0o700)
    first = install(cfg, profile=profile, extension_id=EXTENSION,
                    config_path=config, binary=sys.executable)
    second = install(cfg, profile=other, extension_id="b" * 32,
                     config_path=config, binary=sys.executable)
    conflicts = {"v": 1, "scope": "chrome_google_meet", "sources": [first, second]}
    with Store(cfg.db_path) as store:
        store.set_meta("meeting_browser_conflicts", json.dumps(conflicts))
    original_unlink = Path.unlink
    count = 0

    def fail_unlink(path, *args, **kwargs):
        nonlocal count
        if count == failure:
            raise OSError("injected interruption")
        count += 1
        return original_unlink(path, *args, **kwargs)

    original_set = Store.set_meta

    def fail_final(store, key, value):
        if failure == 3 and key == "meeting_browser_removals" and json.loads(value) == {}:
            raise OSError("injected interruption")
        return original_set(store, key, value)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail_unlink)
        patch.setattr(Store, "set_meta", fail_final)
        with pytest.raises(OSError, match="injected"):
            remove(cfg, profile=profile, extension_id=EXTENSION)
    with pytest.raises(ValueError, match="previous removal"):
        install(cfg, profile=profile, extension_id=EXTENSION,
                config_path=config, binary=sys.executable)
    remove(cfg, profile=profile, extension_id=EXTENSION)
    with Store(cfg.db_path) as store:
        conflicts_meta = store.get_meta("meeting_browser_conflicts")
        bindings_meta = store.get_meta("meeting_browser_bindings")
        assert conflicts_meta is not None and bindings_meta is not None
        assert json.loads(conflicts_meta)["sources"] == [second]
        assert json.loads(bindings_meta) == {second: "b" * 32}


def test_retry_refuses_modified_survivor_before_deleting_any(tmp_path: Path, monkeypatch) -> None:
    cfg, profile, config = _setup(tmp_path)
    install(cfg, profile=profile, extension_id=EXTENSION, config_path=config,
            binary=sys.executable)

    def stop(path, *args, **kwargs):
        raise OSError("interrupted")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", stop)
        with pytest.raises(OSError):
            remove(cfg, profile=profile, extension_id=EXTENSION)
    directory = profile.parent / "NativeMessagingHosts"
    script = directory / f"{HOST_NAME}.sh"
    registration = directory / f"{HOST_NAME}.json"
    script.write_text("changed")
    with pytest.raises(ValueError, match="modified"):
        remove(cfg, profile=profile, extension_id=EXTENSION)
    assert registration.exists()
    assert script.read_text() == "changed"


def test_install_pins_invoking_executable_not_path_shadow(tmp_path: Path, monkeypatch) -> None:
    cfg, profile, config = _setup(tmp_path)
    shadow = tmp_path / "sense"
    shadow.write_text("#!/bin/sh\nexit 1\n")
    shadow.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(sys, "argv", [sys.executable])
    install(cfg, profile=profile, extension_id=EXTENSION, config_path=config)
    script = (profile.parent / "NativeMessagingHosts" / f"{HOST_NAME}.sh").read_text()
    assert os.path.realpath(sys.executable) in script
    assert str(shadow) not in script


def test_cli_registers_explicit_nonstandard_profile(tmp_path: Path, monkeypatch) -> None:
    cfg, profile, config = _setup(tmp_path)
    monkeypatch.setattr(sys, "argv", [sys.executable])
    args = Namespace(meeting_browser_action="install", profile=profile,
                     extension_id=EXTENSION, config=config)
    assert dispatch(args, cfg) == 0
    registration = profile.parent / "NativeMessagingHosts" / f"{HOST_NAME}.json"
    assert json.loads(registration.read_text())["allowed_origins"] == [
        f"chrome-extension://{EXTENSION}/"
    ]
    args.meeting_browser_action = "remove"
    assert dispatch(args, cfg) == 0
    assert not registration.exists()


def test_cli_registers_custom_chrome_for_testing_data_root(tmp_path: Path, monkeypatch) -> None:
    cfg, _, config = _setup(tmp_path)
    root = tmp_path / "chrome-for-testing custom data"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(sys, "argv", [sys.executable])
    args = Namespace(meeting_browser_action="install", profile=None, data_dir=root,
                     extension_id=EXTENSION, config=config)
    assert dispatch(args, cfg) == 0
    registration = root / "NativeMessagingHosts" / f"{HOST_NAME}.json"
    script = root / "NativeMessagingHosts" / f"{HOST_NAME}.sh"
    assert json.loads(registration.read_text())["allowed_origins"] == [
        f"chrome-extension://{EXTENSION}/"
    ]
    assert "--data-dir" in script.read_text()
    args.meeting_browser_action = "remove"
    assert dispatch(args, cfg) == 0
    assert not registration.exists()

    # A different locator for the same user-data root can remove the registration.
    args.meeting_browser_action = "install"
    assert dispatch(args, cfg) == 0
    child = root / "Default"
    child.mkdir(mode=0o700)
    args.meeting_browser_action = "remove"
    args.profile, args.data_dir = child, None
    assert dispatch(args, cfg) == 0
    assert not registration.exists()
