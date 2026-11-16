import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {webcrypto} from 'node:crypto';

globalThis.crypto ??= webcrypto;
const source = await readFile(new URL('../../src/roxabi_sense/collectors/meeting_browser/extension/worker.js', import.meta.url), 'utf8');
const {createCollector} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const tick = () => new Promise((resolve) => setImmediate(resolve));

function fixture() {
  let listener;
  const scheduled = new Map();
  let next = 0;
  const timers = {
    setTimeout(fn, ms) { const id = ++next; scheduled.set(id, {fn, ms}); return id; },
    clearTimeout(id) { scheduled.delete(id); },
    run(ms) { for (const [id, job] of [...scheduled]) if (job.ms === ms) { scheduled.delete(id); job.fn(); } },
  };
  const frames = [];
  const ports = [];
  let tabs = [];
  let sample = () => Promise.reject(new Error('no listener'));
  const api = {
    runtime: {
      id: 'extension',
      onMessage: {addListener(fn) { listener = fn; }},
      connectNative(name) {
        assert.equal(name, 'com.roxabi.sense.meeting_browser');
        const port = {onDisconnect: {addListener(fn) { port.lost = fn; }}, postMessage(frame) { frames.push(frame); }, disconnect() {}};
        ports.push(port);
        return port;
      },
    },
    tabs: {
      query: async () => { if (tabs instanceof Error) throw tabs; return tabs; },
      sendMessage: (...args) => sample(...args),
    },
  };
  return {api, timers, frames, ports, setTabs: (value) => { tabs = value; },
    setSample: (value) => { sample = value; },
    respond(message, sender) { listener(message, sender); }};
}

const sender = {id: 'extension', tab: {id: 4}, frameId: 0, documentId: 'doc', url: 'https://meet.google.com/abc'};
const observed = (nonce) => ({kind: 'meeting-observation', nonce, tab_id: 'e69d16b8-c48a-48f2-ad17-5c6117958034', state: 'joined', basis: 'leave_control'});

test('each complete enumeration samples anew and never replays a joined result', async () => {
  const f = fixture();
  f.setTabs([{id: 4}]);
  f.setSample((_tab, message) => { f.respond(observed(message.nonce), sender); return Promise.resolve(); });
  const collector = createCollector(f.api, f.timers);
  collector.start();
  await tick();
  assert.equal(f.frames[0].tabs[0].state, 'joined');
  f.setSample(() => Promise.reject(new Error('document gone')));
  f.timers.run(5000);
  await tick();
  assert.equal(f.frames[1].seq, 1);
  assert.equal(f.frames[1].complete, true);
  assert.deepEqual(f.frames[1].tabs.map(({state, basis}) => ({state, basis})), [{state: 'unknown', basis: 'no_response'}]);
  collector.stop();
});

test('rejects non-top-frame, foreign origin and stale-document replies', async () => {
  const f = fixture();
  f.setTabs([{id: 4}]);
  f.setSample((_tab, message) => {
    for (const change of [{frameId: 1}, {url: 'https://evil.example/'}, {documentId: ''}, {tab: {id: 5}}]) {
      f.respond(observed(message.nonce), {...sender, ...change});
    }
    return Promise.resolve();
  });
  const collector = createCollector(f.api, f.timers);
  collector.start();
  await tick();
  assert.equal(f.frames.length, 0);
  f.timers.run(2000);
  await tick();
  assert.equal(f.frames[0].tabs[0].basis, 'no_response');
  collector.stop();
});

test('enumeration failure is incomplete and reconnection starts a fresh sequence', async () => {
  const f = fixture();
  f.setTabs(new Error('enumeration failed'));
  const collector = createCollector(f.api, f.timers);
  collector.start();
  await tick();
  assert.deepEqual(f.frames[0], {v: 1, seq: 0, complete: false, tabs: []});
  f.ports[0].lost();
  f.setTabs([]);
  f.timers.run(5000);
  await tick();
  assert.deepEqual(f.frames[1], {v: 1, seq: 0, complete: true, tabs: []});
  collector.stop();
});

test('too many tabs fail closed without sampling', async () => {
  const f = fixture();
  f.setTabs(Array.from({length: 129}, (_, id) => ({id})));
  f.setSample(() => { throw new Error('must not sample'); });
  const collector = createCollector(f.api, f.timers);
  collector.start();
  await tick();
  assert.deepEqual(f.frames[0], {v: 1, seq: 0, complete: false, tabs: []});
  collector.stop();
});
