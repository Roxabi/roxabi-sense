-- Only opaque window routing metadata is sent to the local helper.
local token = nil
local key = nil
local visible = false
local stopped = false
local inflight = false
local executable = nil

local ok, config = pcall(function()
    return tern.json.decode(tern.fs.read(tern.plugin.dir .. "/bridge.json", 4096))
end)
if ok and type(config) == "table" and type(config.executable) == "string" then
    executable = config.executable
end

tern.chrome.window_title(function(win)
    if not visible then return nil end
    local title = win.tab or win.space or "Tern"
    return "[sense:t1:" .. token .. "] " .. title
end)

local function clear()
    visible = false
    tern.chrome.refresh()
end

local dispatch
dispatch = function(cx, prior)
    if stopped or inflight then return end
    local valid, submitted = pcall(function()
        if not cx then return nil end
        return cx:window_key()
    end)
    if not executable or not valid or type(submitted) ~= "string" then
        clear()
        return
    end

    inflight = true
    local action = prior and "renew" or "register"
    local success = pcall(function()
        local payload = tern.json.encode({
            schema = 1, window_key = submitted, token = prior,
            delay_ms = prior and 5000 or 0,
        })
        local started = tern.now()
        tern.process.run({executable, "tern-bridge", action}, {
            stdin = payload, timeout_ms = 8000,
        }, function(result, freshcx)
            inflight = false
            if stopped then return end
            local context_ok, current = pcall(function()
                if not freshcx then return nil end
                return freshcx:window_key()
            end)
            local clock_ok, elapsed = pcall(function() return tern.now() - started end)
            if not context_ok or current ~= submitted or not clock_ok
                or not (elapsed >= 0 and elapsed < 10000)
                or type(result) ~= "table" or result.status ~= 0 or result.timed_out
                or type(result.stdout) ~= "string" or #result.stdout > 4096 then
                clear()
                return
            end
            local parsed, response = pcall(tern.json.decode, result.stdout)
            if not parsed or type(response) ~= "table" or response.schema ~= 1
                or type(response.token) ~= "string" or #response.token ~= 16
                or not response.token:match("^[A-Za-z0-9_-]+$")
                or (prior and response.token ~= prior) then
                clear()
                return
            end
            token = response.token
            key = submitted
            visible = true
            tern.chrome.refresh()
            dispatch(freshcx, token)
        end)
    end)
    if not success then
        inflight = false
        clear()
    end
end

local function tick(cx)
    -- A failed renew retains its known token for terminal unload cleanup;
    -- never start another generation while that authority is still held.
    if token then return end
    dispatch(cx, nil)
end

tern.on("unload", function(_cx)
    if stopped then return end
    stopped = true
    local prior, submitted = token, key
    clear()
    -- A known token gets one terminal cleanup even during a delayed renew.
    -- An unpublished in-flight register can only expire with its bounded lease.
    if prior and submitted and executable then
        pcall(function()
            tern.process.run({executable, "tern-bridge", "unregister"}, {
                stdin = tern.json.encode({
                    schema = 1, window_key = submitted, token = prior, delay_ms = 0,
                }),
                timeout_ms = 2000,
            }, function() end)
        end)
    end
end)
tern.timer(0, tick)
tern.chrome.refresh()
