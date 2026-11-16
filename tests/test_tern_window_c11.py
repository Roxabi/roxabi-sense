"""Exercise the packaged window resource with deterministic SDK doubles."""
import shutil
import subprocess
from pathlib import Path

import pytest

RESOURCE = (
    Path(__file__).resolve().parents[1]
    / "src/roxabi_sense/collectors/tern/resources/window.luau"
)
LUA = next(
    (p for name in ("lua", "lua5.4", "lua5.3", "luajit") if (p := shutil.which(name))),
    None,
)

HARNESS = r'''
local resource, scenario = arg[1], arg[2]
local calls, events, timers = {}, {}, {}
local now, refreshes, decodes = 0, 0, 0
local fail_encode, fail_spawn = false, false
local title
local opaque = string.rep("a", 16)
local response = {schema = 1, token = opaque}
tern = {
    plugin = {dir = "/plugin"},
    fs = {read = function() return "config" end},
    json = {
        decode = function(value)
            if value == "config" then return {executable = "/sense"} end
            decodes = decodes + 1
            if value == "bad" then error("bad JSON") end
            return response
        end,
        encode = function(value)
            if fail_encode then error("encode") end
            return value
        end,
    },
    chrome = {
        window_title = function(fn) title = fn end,
        refresh = function() refreshes = refreshes + 1 end,
    },
    on = function(event, fn) events[event] = fn end,
    timer = function(delay, fn) timers[#timers + 1] = {delay, fn} end,
    now = function() return now end,
    process = {run = function(argv, opts, callback)
        if fail_spawn then error("spawn") end
        calls[#calls + 1] = {argv = argv, opts = opts, callback = callback}
    end},
}
local function cx(key)
    return {window_key = function() return key end}
end
local function complete(index, result, context)
    calls[index].callback(result or {status = 0, stdout = "ok"}, context)
end
assert(loadfile(resource))()
assert(#timers == 1 and timers[1][1] == 0 and refreshes == 1)
assert(events.process_exit == nil)
if scenario == "encode" then fail_encode = true end
if scenario == "spawn" then fail_spawn = true end
timers[1][2](cx(""))
if scenario == "encode" or scenario == "spawn" then
    assert(#calls == 0 and title({}) == nil and refreshes == 2)
    return
end
assert(#calls == 1 and calls[1].argv[3] == "register")
assert(calls[1].opts.stdin.window_key == "" and calls[1].opts.stdin.delay_ms == 0)
assert(calls[1].opts.timeout_ms == 8000)
if scenario == "unload_register" then
    events.unload(nil)
    complete(1, nil, cx(""))
    assert(#calls == 1 and title({}) == nil)
    return
end
local result, context = {status = 0, stdout = "ok"}, cx("")
if scenario == "nil_context" then context = nil end
if scenario == "wrong_key" then context = cx("other") end
if scenario == "context_error" then context = {window_key = function() error("context") end} end
if scenario == "status" then result.status = 1 end
if scenario == "timeout" then result.timed_out = true end
if scenario == "stdout_type" then result.stdout = false end
if scenario == "stdout_large" then result.stdout = string.rep("x", 4097) end
if scenario == "json" then result.stdout = "bad" end
if scenario == "schema" then response.schema = 2 end
if scenario == "token" then response.token = "bad!" end
if scenario == "late" then now = 10000 end
if scenario == "negative" then now = -1 end
complete(1, result, context)
if scenario ~= "success" and scenario ~= "renew_mismatch" and scenario ~= "unload" then
    assert(#calls == 1 and title({}) == nil and refreshes == 2)
    if scenario == "stdout_large" then assert(decodes == 0) end
    return
end
assert(title({tab = "tab"}) == "[sense:t1:" .. opaque .. "] tab")
assert(#calls == 2 and calls[2].argv[3] == "renew")
assert(calls[2].opts.stdin.delay_ms == 5000 and calls[2].opts.stdin.token == opaque)
if scenario == "unload" then
    events.unload(nil)
    assert(title({}) == nil and #calls == 3 and calls[3].argv[3] == "unregister")
    assert(calls[3].opts.stdin.delay_ms == 0 and calls[3].opts.stdin.window_key == "")
    assert(calls[3].opts.stdin.token == opaque and calls[3].opts.timeout_ms == 2000)
    complete(2, nil, cx(""))
    calls[3].callback({status = 0}, cx(""))
    assert(#calls == 3 and title({}) == nil)
    return
end
if scenario == "renew_mismatch" then response.token = string.rep("b", 16) end
now = 5000
complete(2, nil, cx(""))
if scenario == "renew_mismatch" then
    assert(#calls == 2 and title({}) == nil)
else
    assert(#calls == 3 and calls[3].argv[3] == "renew")
    assert(calls[3].opts.stdin.delay_ms == 5000)
end
'''


@pytest.mark.parametrize("scenario", [
    "success", "unload", "unload_register", "encode", "spawn", "nil_context",
    "wrong_key", "context_error", "status", "timeout", "stdout_type", "stdout_large",
    "json", "schema", "token", "late", "negative", "renew_mismatch",
])
def test_window_callback_contract(tmp_path, scenario):
    if LUA is None:
        pytest.skip("Lua interpreter unavailable for packaged resource harness")
    script = tmp_path / "window_harness.lua"
    script.write_text(HARNESS)
    subprocess.run([LUA, str(script), str(RESOURCE), scenario], check=True, timeout=5)
