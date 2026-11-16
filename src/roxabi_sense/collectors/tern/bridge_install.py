"""Explicit, ownership-checked installation of the packaged Tern companion."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import tomllib
from importlib.resources import files
from pathlib import Path

_ID = 'roxabi-sense'
_FILES = ('plugin.toml', 'window.luau', 'bridge.json')


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(['tern', 'plugin', *args], capture_output=True, text=True,
                          timeout=65, check=True)


def _plugins() -> Path:
    result = json.loads(_run('dir', '--json').stdout)
    path = Path(result['dir'])
    if not path.is_absolute():
        raise ValueError('invalid plugin directory')
    return path


def _owned(path: Path) -> bool:
    try:
        if path.is_symlink() or not path.is_dir():
            return False
        if set(p.name for p in path.iterdir()) != {*_FILES, 'ownership.json'}:
            return False
        if any((path / name).is_symlink() or not (path / name).is_file()
               or (path / name).stat().st_size > 65536 for name in (*_FILES, 'ownership.json')):
            return False
        owner = json.loads((path / 'ownership.json').read_text())
        hashes = {
            name: hashlib.sha256((path / name).read_bytes()).hexdigest() for name in _FILES
        }
        return (owner.get('schema') == 1 and owner.get('id') == _ID
                and owner.get('hashes') == hashes)
    except (OSError, ValueError, AttributeError):
        return False


def _check_conflicts(root: Path) -> bool:
    if not root.exists():
        return False
    if root.is_symlink() or not root.is_dir():
        raise ValueError('unsafe plugin directory')
    found = False
    for entry in root.iterdir():
        if entry.name.startswith('.'):
            continue
        if entry.name == _ID:
            if not _owned(entry):
                raise ValueError('plugin id is not an owned copy')
            found = True
            continue
        if entry.name == _ID + '.path':
            raise ValueError('plugin id is linked')
        try:
            manifest = entry / 'plugin.toml'
            if manifest.is_file() and manifest.stat().st_size <= 65536:
                with manifest.open('rb') as stream:
                    if tomllib.load(stream).get('id') == _ID:
                        raise ValueError('plugin id is already used')
            elif entry.suffix == '.path' and entry.is_file() and entry.stat().st_size <= 4096:
                target = Path(entry.read_text().strip())
                manifest = target / 'plugin.toml'
                if target.is_absolute() and manifest.is_file() and manifest.stat().st_size <= 65536:
                    with manifest.open('rb') as stream:
                        if tomllib.load(stream).get('id') == _ID:
                            raise ValueError('plugin id is already linked')
        except (OSError, tomllib.TOMLDecodeError):
            continue
    return found


def install() -> None:
    root = _plugins()
    exists = _check_conflicts(root)
    executable = shutil.which('sense')
    if executable is None:
        raise ValueError('sense executable not found')
    executable = str(Path(executable).resolve(strict=True))
    resource = files('roxabi_sense.collectors.tern').joinpath('resources')
    with tempfile.TemporaryDirectory(prefix='sense-tern-') as temp:
        stage = Path(temp)
        for name in ('plugin.toml', 'window.luau'):
            (stage / name).write_bytes(resource.joinpath(name).read_bytes())
        (stage / 'bridge.json').write_text(json.dumps({'schema': 1, 'executable': executable}))
        hashes = {name: hashlib.sha256((stage / name).read_bytes()).hexdigest() for name in _FILES}
        (stage / 'ownership.json').write_text(
            json.dumps({'schema': 1, 'id': _ID, 'hashes': hashes})
        )
        _run('install', str(stage), *(['--force'] if exists else []))


def remove() -> None:
    root = _plugins()
    if not _check_conflicts(root):
        raise ValueError('owned plugin not installed')
    _run('remove', _ID)


def cli(action: str) -> int:
    try:
        if action == 'install':
            install()
        else:
            remove()
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(f'tern bridge {action} failed: {exc}', file=sys.stderr)
        return 1
