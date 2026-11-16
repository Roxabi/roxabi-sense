-- Only opaque window routing metadata is sent to the local helper.
local token = nil
local key = nil
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
    if not token then return nil end
    local title = win.tab or win.space or "Tern"
    return "[sense:t1:" .. token .. "] " .. title
end)

local function tick(cx)
    if stopped then return end
    if not executable or not cx then
        token = nil
        tern.chrome.refresh()
        tern.timer(5000, tick)
        return
    end
    key = cx:window_key()
    if inflight then return end
    inflight = true
    local action = token and "renew" or "register"
    local prior = token
    local payload = { schema = 1, window_key = key, token = token }
    local started = tern.now()
    local success = pcall(function()
        tern.process.run({executable, "tern-bridge", action}, {
            stdin = tern.json.encode(payload), timeout_ms = 3000,
        }, function(result, _cx)
            inflight = false
            if stopped then return end
            local parsed, response = pcall(tern.json.decode, result.stdout)
            if result.status == 0 and not result.timed_out and parsed
                and type(response) == "table" and response.schema == 1
                and type(response.token) == "string"
                and #response.token == 16 and response.token:match("^[%w_-]+$")
                and (not prior or response.token == prior) and tern.now() - started < 10000 then
                token = response.token
            else
                token = nil
            end
            tern.chrome.refresh()
            tern.timer(5000, tick)
        end)
    end)
    if not success then
        inflight = false
        token = nil
        tern.chrome.refresh()
        tern.timer(5000, tick)
    end
end

tern.on("unload", function(_cx)
    stopped = true
    if token and key and executable then
        pcall(function()
            tern.process.run({executable, "tern-bridge", "unregister"}, {
                stdin = tern.json.encode({schema = 1, window_key = key, token = token}),
                timeout_ms = 2000,
            }, function() end)
        end)
    end
    token = nil
end)
tern.timer(0, tick)
