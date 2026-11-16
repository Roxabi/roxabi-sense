const HOST = 'com.roxabi.sense.meeting_browser';
const CADENCE_MS = 5000;
const DEADLINE_MS = 2000;
const MAX_TABS = 128;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const PAIRS = new Set(['joined:leave_control', 'not_joined:home', 'not_joined:prejoin',
  'not_joined:postcall', 'unknown:unrecognized']);

// Injected Chrome and timers let the same worker protocol be exercised in Node.
export function createCollector(api, timers = globalThis) {
  const pending = new Map();
  let port = null;
  let seq = 0;
  let timer = null;
  let generation = 0;

  api.runtime.onMessage.addListener((message, sender) => {
    if (!message || message.kind !== 'meeting-observation' ||
        typeof message.nonce !== 'string') return;
    const request = pending.get(message.nonce);
    if (!request || sender.id !== api.runtime.id || sender.frameId !== 0 ||
        sender.tab?.id !== request.tab || typeof sender.documentId !== 'string' ||
        !sender.documentId || typeof sender.url !== 'string') return;
    let url;
    try { url = new URL(sender.url); } catch { return; }
    if (url.origin !== 'https://meet.google.com' ||
        Object.keys(message).sort().join(',') !== 'basis,kind,nonce,state,tab_id' ||
        typeof message.tab_id !== 'string' || !UUID.test(message.tab_id) ||
        !PAIRS.has(`${message.state}:${message.basis}`)) return;
    request.finish({tab_id: message.tab_id, state: message.state, basis: message.basis});
  });

  function sample(tab) {
    return new Promise((resolve) => {
      const nonce = crypto.randomUUID();
      const fallback = {tab_id: crypto.randomUUID(), state: 'unknown', basis: 'no_response'};
      let deadline;
      const finish = (result) => {
        if (!pending.delete(nonce)) return;
        timers.clearTimeout(deadline);
        resolve(result);
      };
      pending.set(nonce, {tab, finish});
      deadline = timers.setTimeout(() => finish(fallback), DEADLINE_MS);
      try {
        Promise.resolve(api.tabs.sendMessage(tab, {kind: 'meeting-sample', nonce}, {frameId: 0}))
          .catch(() => finish(fallback));
      } catch { finish(fallback); }
    });
  }

  async function collect(current) {
    let complete = false;
    let tabs = [];
    try {
      const found = await api.tabs.query({url: 'https://meet.google.com/*'});
      if (!Array.isArray(found) || found.length > MAX_TABS ||
          found.some((tab) => !Number.isInteger(tab.id)) ||
          new Set(found.map((tab) => tab.id)).size !== found.length) throw new Error('invalid enumeration');
      tabs = await Promise.all(found.map((tab) => sample(tab.id)));
      complete = true;
    } catch { tabs = []; }
    if (current !== generation || !port) return;
    try { port.postMessage({v: 1, seq: seq++, complete, tabs}); }
    catch { disconnect(); return; }
    timer = timers.setTimeout(() => collect(current), CADENCE_MS);
  }

  function disconnect() {
    generation++;
    if (timer !== null) timers.clearTimeout(timer);
    timer = null;
    const old = port;
    port = null;
    if (old) { try { old.disconnect(); } catch {} }
    // Do not replay any observation across native connections.
    for (const request of [...pending.values()]) request.finish({
      tab_id: crypto.randomUUID(), state: 'unknown', basis: 'no_response',
    });
  }

  function start() {
    disconnect();
    const current = generation;
    try {
      port = api.runtime.connectNative(HOST);
      seq = 0;
      port.onDisconnect.addListener(() => {
        if (current !== generation) return;
        disconnect();
        timer = timers.setTimeout(start, CADENCE_MS);
      });
      collect(current);
    } catch {
      disconnect();
      timer = timers.setTimeout(start, CADENCE_MS);
    }
  }
  return {start, stop: disconnect};
}

if (typeof chrome !== 'undefined' && chrome.runtime?.connectNative) createCollector(chrome).start();
