"""Behavioral coverage for explicit browser connector ownership."""

import json
from pathlib import Path

import pytest

from roxabi_sense.collectors.meeting_browser.install_files import HOST_NAME
from roxabi_sense.collectors.meeting_browser.installer import export_extension, install, remove
from roxabi_sense.config import SenseConfig
from roxabi_sense.store import Store

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
                     config_path=config, binary="/usr/bin/sense")
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
                    config_path=config, binary="/usr/bin/sense")
    second = install(cfg, profile=other, extension_id="b" * 32,
                     config_path=config, binary="/usr/bin/sense")
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
                config_path=config, binary="/usr/bin/sense")
    assert victim.read_text() == "untouched"
    assert script.is_symlink()
    script.unlink()
    script.write_text("unowned")
    with pytest.raises(FileExistsError):
        install(cfg, profile=profile, extension_id=EXTENSION,
                config_path=config, binary="/usr/bin/sense")
    assert script.read_text() == "unowned"


def test_modified_manifest_cannot_be_removed(tmp_path: Path) -> None:
    cfg, profile, config = _setup(tmp_path)
    source = install(cfg, profile=profile, extension_id=EXTENSION,
                     config_path=config, binary="/usr/bin/sense")
    registration = profile.parent / "NativeMessagingHosts" / f"{HOST_NAME}.json"
    registration.write_text("unowned change")
    with pytest.raises(ValueError, match="modified"):
        remove(cfg, profile=profile, extension_id=EXTENSION)
    assert registration.read_text() == "unowned change"
    with Store(cfg.db_path) as store:
        event = store.last_by_kind("meeting_sources")
        assert event is not None
        assert event.payload["sources"] == [source]
