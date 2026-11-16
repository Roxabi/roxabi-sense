import json
import os

import pytest

from roxabi_sense.collectors.tern import bridge


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    monkeypatch.setenv('XDG_RUNTIME_DIR', str(tmp_path))
    clock = [100.0]
    monkeypatch.setattr(bridge, 'boot_identity', lambda: ('boot', clock[0]))
    monkeypatch.setattr(bridge, 'inspect', lambda key: {'clients': [
        {'kind': 'window', 'window': key, 'conn': 4, 'since': 9}
    ]})
    return tmp_path, clock


def test_empty_key_register_renew_and_remove(runtime):
    root, clock = runtime
    response = bridge.request('register', {'schema': 1, 'window_key': ''})
    token = response['token']
    record = bridge.read_registrations()[0]
    assert (record['window_key'], record['conn'], record['since']) == ('', 4, 9)
    path = root / 'roxabi-sense' / 'tern-bridge' / (token + '.json')
    assert path.stat().st_mode & 0o777 == 0o600
    clock[0] += 5
    renewed = bridge.request('renew', {'schema': 1, 'window_key': '', 'token': token})
    assert renewed['token'] == token
    bridge.request('unregister', {'schema': 1, 'window_key': '', 'token': token})
    assert bridge.read_registrations() == []


def test_expired_token_cannot_renew_or_remove_new_generation(runtime):
    _, clock = runtime
    old = bridge.request('register', {'schema': 1, 'window_key': 'x'})['token']
    clock[0] += 16
    new = bridge.request('register', {'schema': 1, 'window_key': 'x'})['token']
    for action in ('renew', 'unregister'):
        with pytest.raises((ValueError, FileNotFoundError)):
            bridge.request(action, {'schema': 1, 'window_key': 'x', 'token': old})
    assert [r['token'] for r in bridge.read_registrations()] == [new]


def test_changed_or_ambiguous_client_fails_closed(runtime, monkeypatch):
    token = bridge.request('register', {'schema': 1, 'window_key': 'a'})['token']
    row = {'kind': 'window', 'window': 'a', 'conn': 5, 'since': 9}
    monkeypatch.setattr(bridge, 'inspect', lambda key: {'clients': [row]})
    with pytest.raises(ValueError):
        bridge.request('renew', {'schema': 1, 'window_key': 'a', 'token': token})
    monkeypatch.setattr(bridge, 'inspect', lambda key: {'clients': [row, row]})
    with pytest.raises(ValueError):
        bridge.request('register', {'schema': 1, 'window_key': 'a'})


def test_reject_unsafe_and_forged_files(runtime):
    root, _ = runtime
    token = bridge.request('register', {'schema': 1, 'window_key': 'x'})['token']
    path = root / 'roxabi-sense' / 'tern-bridge' / (token + '.json')
    path.chmod(0o644)
    assert bridge.read_registrations() == []
    path.unlink()
    target = root / 'target'
    target.write_text(json.dumps({'schema': 1}))
    os.symlink(target, path)
    assert bridge.read_registrations() == []
