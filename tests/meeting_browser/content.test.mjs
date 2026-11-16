import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {runInNewContext} from 'node:vm';
import {webcrypto} from 'node:crypto';

const script = await readFile(new URL('../../src/roxabi_sense/collectors/meeting_browser/extension/content.js', import.meta.url), 'utf8');
function observe(labels, {hidden = [], reconnecting = false} = {}) {
  let handler;
  let result;
  const element = (label) => ({
    label, textContent: label, closest: () => null,
    getBoundingClientRect: () => ({width: hidden.includes(label) ? 0 : 10, height: 10}),
  });
  const chrome = {runtime: {id: 'extension', onMessage: {addListener(fn) { handler = fn; }},
    sendMessage(message) { result = message; return Promise.resolve(); }}};
  runInNewContext(script, {chrome, crypto: webcrypto,
    document: {querySelectorAll(selector) {
      if (selector.includes('[role="alert"]')) return reconnecting ? [element('Reconnecting...')] : [];
      return labels.filter((label) => selector.includes(`aria-label="${label}"`)).map(element);
    }},
    getComputedStyle: () => ({display: 'block', visibility: 'visible', opacity: '1'}),
  });
  handler({kind: 'meeting-sample', nonce: 'request'}, {id: 'extension'});
  let confirmation;
  handler({kind: 'meeting-confirm', nonce: 'request'}, {id: 'extension'},
    (reply) => { confirmation = reply; });
  assert.equal(confirmation.kind, 'meeting-confirmed');
  assert.equal(confirmation.nonce, 'request');
  return {state: result.state, basis: result.basis};
}

test('requires visible unambiguous in-call controls', () => {
  assert.deepEqual(observe(['Leave call']), {state: 'joined', basis: 'leave_control'});
  assert.deepEqual(observe(['Leave call'], {hidden: ['Leave call']}), {state: 'unknown', basis: 'unrecognized'});
  assert.deepEqual(observe(['Leave call'], {reconnecting: true}), {state: 'unknown', basis: 'unrecognized'});
  assert.deepEqual(observe(['Leave call', 'Join now']), {state: 'unknown', basis: 'unrecognized'});
});

test('absence of leave is unknown unless recognized UI is present', () => {
  assert.deepEqual(observe([]), {state: 'unknown', basis: 'unrecognized'});
  assert.deepEqual(observe(['New meeting', 'Enter a code or link']), {state: 'not_joined', basis: 'home'});
  assert.deepEqual(observe(['Ask to join']), {state: 'not_joined', basis: 'prejoin'});
});
