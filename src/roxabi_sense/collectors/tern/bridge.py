"""Private, short-lived Tern window route registrations."""
from __future__ import annotations

import fcntl
import json
import math
import os
import re
import secrets
import stat
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeGuard

from roxabi_sense.collectors.tern.metadata import clients, endpoint_ref
from roxabi_sense.collectors.tern.runner import TernUnavailable, inspect

TOKEN = re.compile(r"^[A-Za-z0-9_-]{16}$")
LEASE = 15.0
LIMIT = 4096


def boot_identity() -> tuple[str, float]:
    return (Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            time.clock_gettime(time.CLOCK_BOOTTIME))


def _directory(create: bool = False) -> Path:
    base = os.environ.get('XDG_RUNTIME_DIR')
    if not base or not os.path.isabs(base):
        raise ValueError('private runtime directory unavailable')
    root = Path(base)
    st = root.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise ValueError('unsafe runtime directory')
    for part in ('roxabi-sense', 'tern-bridge'):
        root = root / part
        if create:
            try:
                root.mkdir(mode=0o700)
            except FileExistsError:
                pass
        st = root.lstat()
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
            raise ValueError('unsafe bridge directory')
    return root


def _read(path: Path) -> dict[str, Any]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid()
                or st.st_mode & 0o077 or st.st_size > LIMIT):
            raise ValueError('unsafe registration')
        raw = os.read(fd, LIMIT + 1)
        if len(raw) > LIMIT:
            raise ValueError('oversized registration')
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError('invalid registration')
        return value
    finally:
        os.close(fd)


@contextmanager
def _locked(root: Path) -> Iterator[None]:
    fd = os.open(root / '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
            raise ValueError('unsafe lock')
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _write(root: Path, token: str, value: dict[str, Any]) -> None:
    data = json.dumps(value, separators=(',', ':')).encode()
    if len(data) > LIMIT:
        raise ValueError('oversized registration')
    fd, name = tempfile.mkstemp(prefix='.registration-', dir=root)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, root / (token + '.json'))
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _numeric_expiry(value: object) -> TypeGuard[int | float]:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and (not isinstance(value, float) or math.isfinite(value)))


def _valid(record: dict[str, Any], token: str, boot: str, now: float) -> bool:
    expiry = record.get('expires_boottime')
    if not _numeric_expiry(expiry):
        return False
    return (record.get('schema') == 1 and record.get('token') == token
            and TOKEN.fullmatch(token) is not None and record.get('boot_id') == boot
            and isinstance(record.get('window_key'), str)
            and len(record['window_key'].encode()) <= 1024
            and isinstance(record.get('endpoint_ref'), str)
            and all(type(record.get(k)) is int and record[k] >= 0 for k in ('conn', 'since'))
            and now < expiry <= now + LEASE)


def read_registrations() -> list[dict[str, Any]]:
    try:
        root = _directory()
        boot, now = boot_identity()
        result = []
        count = 0
        for path in root.iterdir():
            if not path.name.endswith('.json') or not TOKEN.fullmatch(path.stem):
                continue
            count += 1
            if count > 1024:
                return []
            try:
                record = _read(path)
                if _valid(record, path.stem, boot, now):
                    result.append(record)
            except (OSError, ValueError, UnicodeError):
                continue
        return result
    except (OSError, ValueError):
        return []


def request(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get('schema') != 1 or not isinstance(payload.get('window_key'), str):
        raise ValueError('invalid request')
    key = payload['window_key']
    if len(key.encode()) > 1024:
        raise ValueError('invalid window key')
    if action not in ('register', 'renew', 'unregister'):
        raise ValueError('invalid action')
    requested_token = payload.get('token')
    if action != 'register':
        if not isinstance(requested_token, str) or not TOKEN.fullmatch(requested_token):
            raise ValueError('invalid token')
        token = requested_token
    else:
        token = secrets.token_urlsafe(12)
    root = _directory(create=True)
    with _locked(root):
        boot, now = boot_identity()
        if action == 'register':
            count = 0
            for candidate in root.iterdir():
                if candidate.name.endswith('.json') and TOKEN.fullmatch(candidate.stem):
                    count += 1
                    if count > 1024:
                        raise ValueError('too many registrations')
                    try:
                        old = _read(candidate)
                        expiry = old.get('expires_boottime')
                        if (old.get('schema') == 1 and old.get('token') == candidate.stem
                                and (old.get('boot_id') != boot
                                     or (_numeric_expiry(expiry) and expiry <= now))):
                            candidate.unlink()
                    except (OSError, ValueError, UnicodeError):
                        continue
        if action == 'register':
            if (root / (token + '.json')).exists():
                raise ValueError('token collision')
        else:
            path = root / (token + '.json')
            record = _read(path)
            if not _valid(record, token, boot, now) or record['window_key'] != key:
                raise ValueError('expired or mismatched registration')
            if action == 'unregister':
                path.unlink()
                return {'schema': 1, 'ok': True}
        endpoint = endpoint_ref()
        routes = [r for r in clients(inspect(key)) if r['window_key'] == key]
        if len(routes) != 1:
            raise ValueError('ambiguous window client')
        if action == 'renew' and (record.get('endpoint_ref') != endpoint
                                  or any(record[k] != routes[0][k] for k in ('conn', 'since'))):
            raise ValueError('window client changed')
        boot, now = boot_identity()
        if action == 'renew' and not _valid(record, token, boot, now):
            raise ValueError('registration expired')
        record = {'schema': 1, 'token': token, **routes[0], 'endpoint_ref': endpoint,
                  'boot_id': boot, 'expires_boottime': now + LEASE}
        _write(root, token, record)
        return {'schema': 1, 'token': token}


def cli(action: str) -> int:
    import sys
    try:
        raw = sys.stdin.buffer.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise ValueError('oversized request')
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError('invalid request')
        print(json.dumps(request(action, payload), separators=(',', ':')))
        return 0
    except (OSError, ValueError, UnicodeError, TernUnavailable):
        print('tern bridge request failed', file=sys.stderr)
        return 1
