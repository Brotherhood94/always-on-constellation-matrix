import { test } from 'node:test';
import assert from 'node:assert/strict';
import { freshness, historyWithin, httpsLink } from '../dashboard/dashboard.mjs';
import { reconcilePins, shortcutRecipientKey } from '../deploy/matrix/bot/pins.mjs';

test('dashboard accepts the graph link, filters history, and reports stale or missing data', () => {
  assert.equal(httpsLink('https://matrix.example:8444/browser/'), 'https://matrix.example:8444/browser/');
  for (const value of [undefined, 'javascript:alert(1)', 'https://secret@matrix.example/', 'https://matrix.example/?key=secret']) {
    assert.throws(() => httpsLink(value));
  }
  const now = 1800000000000;
  assert.match(freshness(now / 1000 - 181, now), /^Stale since/);
  assert.match(freshness(now / 1000 - 60, now), /^Updated/);
  assert.equal(freshness(undefined, now), 'Unavailable');
  const recent = [now / 1000 - 45, 24];
  assert.deepEqual(historyWithin({ cpu: [[now / 1000 - 86401, 2], recent, [now / 1000, null]] }, 'cpu', 1, now), [recent]);
});

test('encrypted shortcuts wait for operator device keys and update only their own pins', async () => {
  const state = { entries: {}, replaced: [] };
  const links = { dashboard: 'https://matrix.example:8443/', context: 'https://matrix.example:8444/browser/' };
  let joined = false;
  let devices = null;
  let pinned = ['$operator'];
  let writes = 0;
  const messages = new Map();
  const matrix = {
    recipientKey: async () => joined && devices,
    getPinned: async () => pinned,
    send: async (txn, content) => {
      if (!messages.has(txn)) messages.set(txn, content);
      return '$' + txn;
    },
    setPinned: async events => { pinned = events; writes++; },
    save: async () => {},
  };
  assert.equal(await reconcilePins(matrix, links, state), false);
  assert.equal(messages.size, 0);
  joined = true;
  assert.equal(await reconcilePins(matrix, links, state), false);
  assert.equal(messages.size, 0);
  devices = 'first-device';
  await reconcilePins(matrix, links, state);
  assert.equal(messages.size, 1);
  const [content] = messages.values();
  assert.ok(content.formatted_body.includes(`<a href="${links.dashboard}">Open dashboard</a>`));
  assert.ok(content.formatted_body.includes(`<a href="${links.context}">Open Context Intelligence</a>`));
  assert.equal(pinned.length, 2);
  const [operator, first] = pinned;
  assert.equal(state.entries.dashboard.event, first);
  assert.equal(state.entries.context.event, first);
  await reconcilePins(matrix, links, state);
  assert.equal(writes, 1);
  const updated = { ...links, dashboard: 'https://matrix.example:9443/' };
  await reconcilePins(matrix, updated, state);
  assert.equal(messages.size, 2);
  assert.deepEqual(pinned, [operator, state.entries.dashboard.event]);
  assert.ok(!pinned.includes(first));
  const previousPins = [...pinned];
  devices = 'two-devices';
  await reconcilePins(matrix, updated, state);
  assert.equal(messages.size, 3);
  assert.deepEqual(pinned.filter(event => previousPins.includes(event)), [operator]);
  await reconcilePins(matrix, updated, state);
  assert.equal(writes, 3);
});

test('shortcut recipients use stable public device keys', () => {
  const owner = '@operator:fixture.test';
  const device = id => ({ user_id: owner, device_id: id, algorithms: ['m.megolm.v1.aes-sha2'],
    keys: { [`curve25519:${id}`]: 'curve-' + id, [`ed25519:${id}`]: 'signing-' + id } });
  const first = device('A');
  const second = device('B');
  assert.equal(shortcutRecipientKey(owner, { A: { ...first, user_id: '@unrelated:fixture.test' }, B: {} }), null);
  assert.notEqual(shortcutRecipientKey(owner, { A: first }), shortcutRecipientKey(owner, { A: first, B: second }));
  assert.equal(shortcutRecipientKey(owner, { B: second, A: first }), shortcutRecipientKey(owner, { A: first, B: second }));
});
