import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {runInNewContext} from 'node:vm';
import {webcrypto} from 'node:crypto';

const script = await readFile(new URL('../../src/roxabi_sense/collectors/meeting_browser/extension/content.js', import.meta.url), 'utf8');
function observe(labels, {hidden = [], disabled = [], ariaDisabled = [], fieldset = [],
  ancestors = {}, statuses = [], reconnecting = false} = {}) {
  let handler;
  let result;
  const inputLabels = new Set(['Enter a code or link', 'Enter a code or nickname',
    'Saisissez un code ou un alias']);
  const element = (label, tag = 'button') => ({
    tag, label, textContent: label, parentElement: ancestors[label] || null,
    closest(selector) {
      if (selector.includes('aria-disabled')) return ariaDisabled.includes(label) ? this : null;
      return hidden.includes(label) ? this : null;
    },
    matches(selector) { return selector === ':disabled' &&
      (disabled.includes(label) || fieldset.includes(label)); },
    getBoundingClientRect: () => ({width: 10, height: 10}),
  });
  const elements = labels.map((label) => element(label, inputLabels.has(label) ? 'input' : 'button'));
  const alerts = [...statuses, ...(reconnecting ? ['Reconnecting...'] : [])]
    .map((label) => element(label, 'status'));
  const chrome = {runtime: {id: 'extension', onMessage: {addListener(fn) { handler = fn; }},
    sendMessage(message) { result = message; return Promise.resolve(); }}};
  runInNewContext(script, {chrome, crypto: webcrypto,
    document: {querySelectorAll(selector) {
      if (selector === '[role="alert"], [role="status"]') return alerts;
      const match = /^(button|input|\[role="button"\]|\[role="textbox"\])\[aria-label="([^"]+)"\]$/.exec(selector);
      if (!match) throw new Error(`Unexpected selector: ${selector}`);
      return elements.filter((item) => item.tag === match[1] && item.label === match[2]);
    }},
    getComputedStyle: (node) => node.style || {display: 'block', visibility: 'visible', opacity: '1'},
  });
  handler({kind: 'meeting-sample', nonce: 'request'}, {id: 'extension'});
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

test('recognizes current French and English home controls with a disabled generic join', () => {
  for (const [button, input, join] of [
    ['Nouvelle réunion', 'Saisissez un code ou un alias', 'Participer'],
    ['New meeting', 'Enter a code or nickname', 'Join'],
  ]) {
    assert.deepEqual(observe([button, input, join], {disabled: [join]}),
      {state: 'not_joined', basis: 'home'});
  }
});

test('specific French and English call and prejoin controls have equal semantics', () => {
  for (const label of ['Leave call', "Quitter l'appel", 'Quitter la réunion']) {
    assert.deepEqual(observe([label]), {state: 'joined', basis: 'leave_control'});
  }
  for (const label of ['Join now', 'Ask to join', 'Participer maintenant', 'Demander à participer']) {
    assert.deepEqual(observe([label]), {state: 'not_joined', basis: 'prejoin'});
  }
  assert.deepEqual(observe(['New meeting', 'Saisissez un code ou un alias']),
    {state: 'not_joined', basis: 'home'});
  assert.deepEqual(observe(['Nouvelle réunion', 'Enter a code or nickname']),
    {state: 'not_joined', basis: 'home'});
});

test('disabled, hidden, conflicting and reconnecting controls fail closed', () => {
  const unknown = {state: 'unknown', basis: 'unrecognized'};
  for (const label of ['Leave call', "Quitter l'appel", 'Join now', 'Participer maintenant']) {
    assert.deepEqual(observe([label], {hidden: [label]}), unknown);
    assert.deepEqual(observe([label], {disabled: [label]}), unknown);
    assert.deepEqual(observe([label], {ariaDisabled: [label]}), unknown);
    assert.deepEqual(observe([label], {fieldset: [label]}), unknown);
    assert.deepEqual(observe([label], {ancestors: {
      [label]: {style: {display: 'block', visibility: 'visible', opacity: '0'}, parentElement: null},
    }}), unknown);
  }
  assert.deepEqual(observe(["Quitter l'appel", 'Join now']), unknown);
  assert.deepEqual(observe(['Join']), unknown);
  assert.deepEqual(observe(['Participer']), unknown);
  assert.deepEqual(observe(['New meeting', 'Enter a code or nickname'],
    {ariaDisabled: ['New meeting']}), unknown);
  assert.deepEqual(observe(['Nouvelle réunion', 'Saisissez un code ou un alias'],
    {hidden: ['Saisissez un code ou un alias']}), unknown);
  assert.deepEqual(observe(['Nouvelle réunion', 'Enter a code or nickname', 'Leave call']), unknown);
  assert.deepEqual(observe(['Leave call'], {statuses: ['Reconnexion en cours']}), unknown);
  assert.deepEqual(observe(["Quitter l'appel"], {statuses: ['Reconnecting...']}), unknown);
  assert.deepEqual(observe(['Leave call'], {
    statuses: ['Reconnexion en cours'], hidden: ['Reconnexion en cours'],
  }), {state: 'joined', basis: 'leave_control'});
});
