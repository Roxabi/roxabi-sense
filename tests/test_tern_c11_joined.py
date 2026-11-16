"""Join window callbacks to actual private bridge-record mutations."""
import shutil
import subprocess
from pathlib import Path

import pytest

from roxabi_sense.collectors.tern import bridge

RESOURCE = (
    Path(__file__).resolve().parents[1]
    / 'src/roxabi_sense/collectors/tern/resources/window.luau'
)
LUA = next((p for name in ('lua', 'lua5.4', 'lua5.3', 'luajit') if (p := shutil.which(name))), None)

HARNESS = r'''
local calls, events, timers = {}, {}, {}
local title
tern = {
  plugin = {dir = "/plugin"},
  fs = {read = function() return "config" end},
  json = {
    decode = function(value)
      if value == "config" then return {executable = "/sense"} end
      return {schema = 1, token = value}
    end,
    encode = function(value) return value end,
  },
  chrome = {window_title = function(fn) title = fn end, refresh = function() end},
  on = function(event, fn) events[event] = fn end,
  timer = function(delay, fn) timers[#timers + 1] = fn end,
  now = function() return 0 end,
  process = {run = function(argv, opts, callback)
    calls[#calls + 1] = {callback = callback, opts = opts, action = argv[3]}
    local p = opts.stdin
    io.write(argv[3], "\t", p.window_key, "\t", p.token or "-", "\t", p.delay_ms, "\n")
    io.flush()
  end},
}
local cx = {window_key = function() return "x" end}
assert(loadfile(arg[1]))()
timers[1](cx)
local token = assert(io.read("*l"))
calls[1].callback({status = 0, stdout = token}, cx)
assert(title({}) ~= nil)
local renewed = assert(io.read("*l"))
assert(renewed == token)
calls[2].callback({status = 0, stdout = renewed}, nil)
assert(title({}) == nil)
events.unload(nil)
assert(#calls == 3 and calls[3].action == "unregister")
'''


def test_failed_renew_callback_unload_revokes_real_record(tmp_path, monkeypatch):
    if LUA is None:
        pytest.skip('Lua interpreter unavailable for packaged resource harness')
    tmp_path.chmod(0o700)
    monkeypatch.setenv('XDG_RUNTIME_DIR', str(tmp_path))
    clock = [100.0]
    monkeypatch.setattr(bridge, 'boot_identity', lambda: ('boot', clock[0]))
    monkeypatch.setattr(bridge, 'inspect', lambda key, **kwargs: {'clients': [
        {'kind': 'window', 'window': key, 'conn': 4, 'since': 9},
    ]})
    monkeypatch.setattr(
        bridge.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )
    script = tmp_path / 'joined.lua'
    script.write_text(HARNESS)
    proc = subprocess.Popen([LUA, str(script), str(RESOURCE)], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        def action(expected):
            assert proc.stdout is not None
            fields = proc.stdout.readline().rstrip('\n').split('\t')
            assert len(fields) == 4 and fields[0] == expected
            return {'schema': 1, 'window_key': fields[1], 'delay_ms': int(fields[3]),
                    **({'token': fields[2]} if fields[2] != '-' else {})}

        assert proc.stdin is not None
        registered = bridge.request('register', action('register'))
        token = registered['token']
        proc.stdin.write(token + '\n')
        proc.stdin.flush()
        renewed = bridge.request('renew', action('renew'))
        assert renewed['token'] == token
        assert bridge.read_registrations()[0]['expires_boottime'] == 120
        proc.stdin.write(token + '\n')
        proc.stdin.flush()
        payload = action('unregister')
        assert payload['token'] == token and payload['window_key'] == 'x'
        bridge.request('unregister', payload)
        assert bridge.read_registrations() == []
        _, stderr = proc.communicate(timeout=5)
        assert proc.returncode == 0, stderr
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
