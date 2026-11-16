import json
import os
from contextlib import contextmanager

import pytest

from roxabi_sense.collectors.tern import bridge


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    monkeypatch.setenv('XDG_RUNTIME_DIR', str(tmp_path))
    clock = [100.0]
    monkeypatch.setattr(bridge, 'boot_identity', lambda: ('boot', clock[0]))
    monkeypatch.setattr(bridge, 'inspect', lambda key, **kwargs: {'clients': [
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
    monkeypatch.setattr(bridge, 'inspect', lambda key, **kwargs: {'clients': [row]})
    with pytest.raises(ValueError):
        bridge.request('renew', {'schema': 1, 'window_key': 'a', 'token': token})
    monkeypatch.setattr(bridge, 'inspect', lambda key, **kwargs: {'clients': [row, row]})
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


@pytest.mark.parametrize('action,changes', [
    ('renew', {'delay_ms': True}), ('renew', {'delay_ms': 5000.0}),
    ('renew', {'delay_ms': '5000'}), ('renew', {'delay_ms': -1}),
    ('renew', {'delay_ms': 1}), ('register', {'delay_ms': 5000}),
    ('unregister', {'delay_ms': 5000}), ('invalid', {'delay_ms': 5000}),
    ('renew', {'schema': True, 'delay_ms': 5000}),
    ('renew', {'token': 'bad', 'delay_ms': 5000}),
])
def test_invalid_requests_do_not_wait_or_mutate(runtime, monkeypatch, action, changes):
    root, _ = runtime
    token = bridge.request('register', {'schema': 1, 'window_key': 'x'})['token']
    before = (root / 'roxabi-sense' / 'tern-bridge' / (token + '.json')).read_bytes()
    monkeypatch.setattr(bridge.time, 'sleep', lambda _: pytest.fail('unexpected wait'))
    monkeypatch.setattr(bridge, 'inspect', lambda *a, **k: pytest.fail('unexpected inspection'))
    payload = {'schema': 1, 'window_key': 'x', 'token': token}
    payload.update(changes)
    with pytest.raises(ValueError):
        bridge.request(action, payload)
    assert (root / 'roxabi-sense' / 'tern-bridge' / (token + '.json')).read_bytes() == before


@pytest.mark.parametrize('change', ['expiry', 'suspend', 'boot'])
def test_wait_revalidates_lease_and_boot(runtime, monkeypatch, change):
    _, clock = runtime
    token = bridge.request('register', {'schema': 1, 'window_key': 'x'})['token']
    if change == 'expiry':
        clock[0] += 11
    def sleep(seconds):
        assert seconds == 5
        clock[0] += 9 if change == 'suspend' else 5
        if change == 'boot':
            monkeypatch.setattr(bridge, 'boot_identity', lambda: ('other', clock[0]))
    monkeypatch.setattr(bridge.time, 'sleep', sleep)
    monkeypatch.setattr(bridge, 'inspect', lambda *a, **k: pytest.fail('unexpected inspection'))
    with pytest.raises(ValueError):
        bridge.request('renew', {'schema': 1, 'window_key': 'x', 'token': token, 'delay_ms': 5000})


def test_unregister_during_wait_preserves_new_generation(runtime, monkeypatch):
    _, clock = runtime
    old = bridge.request('register', {'schema': 1, 'window_key': 'x'})['token']
    new = []
    def sleep(seconds):
        clock[0] += seconds
        bridge.request('unregister', {'schema': 1, 'window_key': 'x', 'token': old})
        new.append(bridge.request('register', {'schema': 1, 'window_key': 'x'})['token'])
    monkeypatch.setattr(bridge.time, 'sleep', sleep)
    with pytest.raises((ValueError, FileNotFoundError)):
        bridge.request('renew', {'schema': 1, 'window_key': 'x', 'token': old, 'delay_ms': 5000})
    assert [r['token'] for r in bridge.read_registrations()] == new


def test_inspection_deadline_and_unlocked_revocation(runtime, monkeypatch):
    _, clock = runtime
    old = bridge.request('register', {'schema': 1, 'window_key': 'x'})['token']
    original_inspect = bridge.inspect
    def inspect_expired(key, *, deadline):
        assert deadline == 108
        clock[0] = deadline
        return original_inspect(key, deadline=deadline)
    monkeypatch.setattr(bridge, 'inspect', inspect_expired)
    with pytest.raises(ValueError):
        bridge.request('renew', {'schema': 1, 'window_key': 'x', 'token': old})
    assert bridge.read_registrations()[0]['expires_boottime'] == 115

    clock[0] = 109
    def inspect_revoked(key, *, deadline):
        bridge.request('unregister', {'schema': 1, 'window_key': key, 'token': old})
        return original_inspect(key, deadline=deadline)
    monkeypatch.setattr(bridge, 'inspect', inspect_revoked)
    with pytest.raises((ValueError, FileNotFoundError)):
        bridge.request('renew', {'schema': 1, 'window_key': 'x', 'token': old})
    assert bridge.read_registrations() == []


def test_external_inspection_never_holds_lock(runtime, monkeypatch):
    held = [False]
    original_lock = bridge._locked
    original_endpoint = bridge.endpoint_ref
    original_inspect = bridge.inspect

    @contextmanager
    def lock(root):
        with original_lock(root):
            held[0] = True
            try:
                yield
            finally:
                held[0] = False

    def endpoint():
        assert not held[0]
        return original_endpoint()

    def inspect(key, *, deadline):
        assert not held[0]
        return original_inspect(key, deadline=deadline)

    monkeypatch.setattr(bridge, '_locked', lock)
    monkeypatch.setattr(bridge, 'endpoint_ref', endpoint)
    monkeypatch.setattr(bridge, 'inspect', inspect)
    token = bridge.request('register', {'schema': 1, 'window_key': 'x'})['token']
    bridge.request('renew', {'schema': 1, 'window_key': 'x', 'token': token})
