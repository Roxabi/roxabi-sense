"""Behavioral coverage for explicit browser connector ownership."""

import json
import os
import signal
import subprocess
import sys
import textwrap
from argparse import Namespace
from pathlib import Path

import pytest

from roxabi_sense.collectors.meeting_browser.install_files import HOST_NAME
from roxabi_sense.collectors.meeting_browser.installer import (
    export_extension,
    install,
    remove,
    validate_installation,
)
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


def test_deleted_profile_does_not_invalidate_browser_wide_installation(tmp_path: Path) -> None:
    cfg, profile, config = _setup(tmp_path)
    install(cfg, profile=profile, extension_id=EXTENSION,
            config_path=config, binary=sys.executable)
    profile.rmdir()
    assert validate_installation(cfg, profile=profile, extension_id=EXTENSION)
    remove(cfg, profile=profile.parent, extension_id=EXTENSION, data_dir=True)
    assert not (profile.parent / "NativeMessagingHosts" / f"{HOST_NAME}.json").exists()


@pytest.mark.parametrize("checkpoint", [
    "intent", "partial", "link0", "unlink0", "link1", "unlink1",
    "link2", "unlink2", "publish", "committed",
])
def test_install_recovers_after_sigkill(tmp_path: Path, checkpoint: str) -> None:
    cfg, profile, config = _setup(tmp_path)
    child = textwrap.dedent("""
        import os, signal, sys
        from pathlib import Path
        from roxabi_sense.config import SenseConfig
        import roxabi_sense.collectors.meeting_browser.installer as installer
        from roxabi_sense.store import Store

        checkpoint, db, profile, config = sys.argv[1:]
        def die():
            os.kill(os.getpid(), signal.SIGKILL)
        original_open, original_link, original_fdopen = os.open, os.link, os.fdopen
        original_unlink, original_save = Path.unlink, installer._save
        original_exit = Store.__exit__
        links = unlinks = 0
        def opening(path, flags, *args, **kwargs):
            if checkpoint == "intent" and flags & os.O_EXCL:
                die()
            return original_open(path, flags, *args, **kwargs)
        def linking(*args, **kwargs):
            global links
            result = original_link(*args, **kwargs)
            if checkpoint == f"link{links}":
                die()
            links += 1
            return result
        def unlinking(path, *args, **kwargs):
            global unlinks
            result = original_unlink(path, *args, **kwargs)
            if checkpoint == f"unlink{unlinks}":
                die()
            unlinks += 1
            return result
        class Partial:
            def __init__(self, stream): self.stream = stream
            def __enter__(self): return self
            def __exit__(self, *args): self.stream.close()
            def write(self, data):
                self.stream.write(data[:max(1, len(data) // 2)])
                self.stream.flush()
                die()
        def fdopening(fd, mode, *args, **kwargs):
            stream = original_fdopen(fd, mode, *args, **kwargs)
            return Partial(stream) if checkpoint == "partial" and mode == "wb" else stream
        def saving(*args, **kwargs):
            if checkpoint == "publish":
                die()
            return original_save(*args, **kwargs)
        def exiting(store, *args):
            result = original_exit(store, *args)
            if checkpoint == "committed":
                die()
            return result
        os.open, os.link, os.fdopen = opening, linking, fdopening
        Path.unlink, installer._save = unlinking, saving
        Store.__exit__ = exiting
        installer.install(SenseConfig(db_path=Path(db)), profile=Path(profile),
                          extension_id="a" * 32, config_path=Path(config),
                          binary=sys.executable)
    """)
    result = subprocess.run([sys.executable, "-c", child, checkpoint, str(cfg.db_path),
                             str(profile), str(config)], check=False)
    assert result.returncode == -signal.SIGKILL
    if checkpoint != "committed":
        with pytest.raises(ValueError, match="previous installation"):
            install(cfg, profile=profile, extension_id=EXTENSION,
                    config_path=config, binary=sys.executable)
    remove(cfg, profile=profile, extension_id=EXTENSION)
    directory = profile.parent / "NativeMessagingHosts"
    assert not list(directory.iterdir())
    install(cfg, profile=profile, extension_id=EXTENSION,
            config_path=config, binary=sys.executable)
    remove(cfg, profile=profile, extension_id=EXTENSION)


def test_interrupted_install_refuses_modified_or_unowned_survivors(
    tmp_path: Path, monkeypatch
) -> None:
    cfg, profile, config = _setup(tmp_path)
    original_link = os.link
    calls = 0

    def interrupt(source, destination, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("interrupted")
        return original_link(source, destination, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "link", interrupt)
        with pytest.raises(OSError, match="interrupted"):
            install(cfg, profile=profile, extension_id=EXTENSION,
                    config_path=config, binary=sys.executable)

    directory = profile.parent / "NativeMessagingHosts"
    registration = directory / f"{HOST_NAME}.json"
    script = directory / f"{HOST_NAME}.sh"
    original = registration.read_bytes()
    registration.write_bytes(b"modified")
    with pytest.raises(ValueError):
        remove(cfg, profile=profile, extension_id=EXTENSION)
    assert script.exists() is False
    assert list(directory.glob(f".{HOST_NAME}.*"))
    registration.write_bytes(original)
    script.write_bytes(b"unowned")
    with pytest.raises(ValueError):
        remove(cfg, profile=profile, extension_id=EXTENSION)
    assert registration.read_bytes() == original
    assert script.read_bytes() == b"unowned"
