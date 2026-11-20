import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {webcrypto} from 'node:crypto';
import {runInNewContext} from 'node:vm';
import {Worker} from 'node:worker_threads';

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
  let confirm = (_tab, message) => Promise.resolve({kind: 'meeting-confirmed', nonce: message.nonce});
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
      sendMessage: (tab, message, options) => message.kind === 'meeting-confirm'
        ? confirm(tab, message, options) : sample(tab, message, options),
    },
  };
  return {api, timers, frames, ports, setTabs: (value) => { tabs = value; },
    setSample: (value) => { sample = value; },
    setConfirm: (value) => { confirm = value; },
    respond(message, sender) { listener(message, sender); }};
}

const sender = {id: 'extension', tab: {id: 4}, frameId: 0, documentId: 'doc', url: 'https://meet.google.com/abc'};
const observed = (nonce) => ({kind: 'meeting-observation', nonce, tab_id: 'e69d16b8-c48a-48f2-ad17-5c6117958034', state: 'joined', basis: 'leave_control'});

test('Chrome startup wakes the worker and preserves one native session per generation', async () => {
  const worker = new Worker(`
    const {parentPort, workerData} = require('node:worker_threads');
    const assert = require('node:assert/strict');
    const {webcrypto} = require('node:crypto');
    const fixture = ${fixture.toString()};
    const f = fixture();
    const listeners = [];
    let synchronous = true;
    let startupRegisteredSynchronously = false;
    globalThis.markModuleEvaluation = () => queueMicrotask(() => { synchronous = false; });
    f.api.runtime.onStartup = {addListener(listener) {
      startupRegisteredSynchronously ||= synchronous;
      listeners.push(listener);
    }};
    globalThis.chrome = f.api;
    globalThis.crypto = webcrypto;
    globalThis.setTimeout = f.timers.setTimeout;
    globalThis.clearTimeout = f.timers.clearTimeout;
    (async () => {
      await import(workerData.url);
      await new Promise((resolve) => setImmediate(resolve));
      const initial = {frames: structuredClone(f.frames), ports: f.ports.length};
      for (const listener of listeners) listener();
      for (const listener of listeners) listener();
      await new Promise((resolve) => setImmediate(resolve));
      f.timers.run(5000);
      await new Promise((resolve) => setImmediate(resolve));
      parentPort.postMessage({startupRegisteredSynchronously, initial, frames: f.frames, ports: f.ports.length});
    })().catch((error) => { throw error; });
  `, {eval: true, workerData: {url: `data:text/javascript;base64,${Buffer.from(`globalThis.markModuleEvaluation();\n${source}`).toString('base64')}`}});
  const result = await new Promise((resolve, reject) => {
    worker.once('message', resolve);
    worker.once('error', reject);
    worker.once('exit', (code) => { if (code !== 0) reject(new Error(`Worker exited with ${code}`)); });
  });
  assert.equal(result.startupRegisteredSynchronously, true);
  assert.deepEqual(result.initial, {
    frames: [{v: 1, seq: 0, complete: true, tabs: []}],
    ports: 1,
  });
  assert.deepEqual(result.frames, [
    {v: 1, seq: 0, complete: true, tabs: []},
    {v: 1, seq: 1, complete: true, tabs: []},
  ]);
  assert.equal(result.ports, 1);
});

test('real content confirmation gates home observations sent by the worker', async () => {
  const content = await readFile(new URL(
    '../../src/roxabi_sense/collectors/meeting_browser/extension/content.js', import.meta.url), 'utf8');
  for (const confirmAvailable of [true, false]) {
    const f = fixture();
    f.setTabs([{id: 4}]);
    let contentHandler;
    const controls = [
      {tag: 'button', label: 'Nouvelle réunion'},
      {tag: 'input', label: 'Saisissez un code ou un alias'},
    ].map((item) => ({
      ...item, parentElement: null, closest: () => null, matches: () => false,
      getBoundingClientRect: () => ({width: 10, height: 10}),
    }));
    const chrome = {runtime: {
      id: 'extension',
      onMessage: {addListener(fn) { contentHandler = fn; }},
      sendMessage(message) { f.respond(message, sender); return Promise.resolve(); },
    }};
    runInNewContext(content, {
      chrome, crypto: webcrypto,
      document: {querySelectorAll(selector) {
        if (selector === '[role="alert"], [role="status"]') return [];
        const match = /^(button|input|\[role="button"\]|\[role="textbox"\])\[aria-label="([^"]+)"\]$/.exec(selector);
        if (!match) throw new Error(`Unexpected selector: ${selector}`);
        return controls.filter((item) => item.tag === match[1] && item.label === match[2]);
      }},
      getComputedStyle: () => ({display: 'block', visibility: 'visible', opacity: '1'}),
    });
    f.setSample((_tab, message) => {
      contentHandler(message, {id: 'extension'});
      return Promise.resolve();
    });
    f.setConfirm((_tab, message) => new Promise((resolve) => {
      if (confirmAvailable) contentHandler(message, {id: 'extension'}, resolve);
      else resolve(undefined);
    }));
    const collector = createCollector(f.api, f.timers);
    collector.start();
    await tick();
    if (!confirmAvailable) {
      f.timers.run(2000);
      await tick();
    }
    assert.deepEqual(f.frames[0].tabs.map(({state, basis}) => ({state, basis})),
      [confirmAvailable ? {state: 'not_joined', basis: 'home'} :
        {state: 'unknown', basis: 'no_response'}]);
    collector.stop();
  }
});

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

test('rejects non-top-frame, foreign origin and invalid document replies', async () => {
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

test('navigation rejects an old nonempty document reply within the sampling deadline', async () => {
  const f = fixture();
  f.setTabs([{id: 4}]);
  let currentDocument = 'old-document';
  f.setSample((_tab, message) => {
    currentDocument = 'new-document';
    f.respond(observed(message.nonce), {...sender, documentId: 'old-document'});
    return Promise.resolve();
  });
  f.setConfirm((_tab, message, options) => {
    assert.equal(options.documentId, 'old-document');
    return options.documentId === currentDocument
      ? Promise.resolve({kind: 'meeting-confirmed', nonce: message.nonce})
      : Promise.reject(new Error('document no longer exists'));
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

test('synchronous native write failure retries once despite stale disconnect callback', async () => {
  const f = fixture();
  const connect = f.api.runtime.connectNative;
  f.api.runtime.connectNative = (name) => {
    const port = connect(name);
    if (f.ports.length === 1) {
      port.postMessage = () => { throw new Error('disconnected'); };
    }
    return port;
  };
  const collector = createCollector(f.api, f.timers);
  collector.start();
  await tick();
  const staleDisconnect = f.ports[0].lost;
  f.timers.run(5000);
  await tick();
  assert.deepEqual(f.frames, [{v: 1, seq: 0, complete: true, tabs: []}]);
  staleDisconnect();
  f.timers.run(5000);
  await tick();
  assert.equal(f.ports.length, 2);
  assert.deepEqual(f.frames[1], {v: 1, seq: 1, complete: true, tabs: []});
  collector.stop();
  staleDisconnect();
  f.timers.run(5000);
  assert.equal(f.ports.length, 2);
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
