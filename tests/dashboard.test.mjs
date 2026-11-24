import { test } from 'node:test';
import assert from 'node:assert/strict';
import { freshness, historyWithin, httpsLink } from '../dashboard/dashboard.mjs';
import { reconcilePins } from '../deploy/matrix/bot/pins.mjs';

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

test('two encrypted shortcut events wait for membership, survive reruns and replace only their own pins', async () => {
  const state = { entries: {}, replaced: [] };
  const links = { dashboard: 'https://matrix.example:8443/', context: 'https://matrix.example:8444/browser/' };
  let joined = false;
  let pinned = ['$operator'];
  let writes = 0;
  const messages = new Map();
  const matrix = {
    ownerJoined: async () => joined,
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
  await reconcilePins(matrix, links, state);
  assert.equal(messages.size, 2);
  assert.match([...messages.values()][1].formatted_body, /Open Context Intelligence/);
  assert.equal(pinned.length, 3);
  const [operator, dashboard, context] = pinned;
  await reconcilePins(matrix, links, state);
  assert.equal(writes, 1);
  await reconcilePins(matrix, { ...links, dashboard: 'https://matrix.example:9443/' }, state);
  assert.equal(messages.size, 3);
  assert.deepEqual(pinned.slice(0, 2), [operator, context]);
  assert.ok(!pinned.includes(dashboard));
});
