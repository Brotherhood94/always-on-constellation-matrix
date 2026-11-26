import { test } from 'node:test';
import assert from 'node:assert/strict';
import { freshness, historyWithin, httpsLink } from '../dashboard/dashboard.mjs';
import { reconcilePins, reconcileTelegramPin, shortcutRecipientKey } from '../deploy/matrix/bot/pins.mjs';

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

test('Element guides include copyable commands and manage optional pins independently', async () => {
  const links = { dashboard: 'https://matrix.example:8443/', context: 'https://matrix.example:8444/browser/' };
  const command = "sudo ./constellation_matrix --data-dir '/srv/test & workspace'";
  const graphPassword = "sudo cat -- '/srv/test & workspace/context/neo4j-password'";
  const state = { entries: {}, replaced: [] };
  const telegramState = { entries: {}, replaced: [] };
  const messages = new Map();
  let pinned = ['$operator'];
  const matrix = {
    recipientKey: async () => 'device-key', getPinned: async () => pinned,
    send: async (txn, content) => { messages.set('$' + txn, content); return '$' + txn; },
    setPinned: async events => { pinned = events; }, save: async () => {},
  };
  await reconcilePins(matrix, links, state, { command, graphPassword, dot: true, systemd: true });
  const workspace = messages.get(state.entries.dashboard.event);
  assert.match(workspace.body, /Sign in as neo4j/);
  assert.ok(workspace.body.includes(graphPassword));
  assert.ok(workspace.formatted_body.includes('test &amp; workspace'));
  assert.equal(state.entries.dashboard.event, state.entries.context.event);
  const dot = messages.get(state.entries['dot-setup'].event);
  assert.match(dot.body, /Tunnels Read \+ Manage/);
  assert.match(dot.body, /Tunnels Read \+ Use/);
  assert.match(dot.body, /https:\/\/platform\.openai\.com\/settings\/organization\/tunnels/);
  assert.match(dot.body, /https:\/\/chatgpt\.com\/plugins/);
  assert.match(dot.body, /list_agents/);
  assert.ok(dot.body.includes(`${command} dot-connect tunnel_REPLACE_WITH_YOUR_ID`));
  assert.equal(pinned.length, 3);
  await reconcilePins(matrix, links, state, { command, graphPassword, dot: false });
  assert.deepEqual(pinned, ['$operator', state.entries.dashboard.event]);
  assert.equal(messages.size, 2);

  pinned = ['$telegram-operator'];
  await reconcileTelegramPin(matrix, telegramState, command);
  const guide = messages.get(telegramState.entries['telegram-setup'].event);
  assert.match(guide.body, /https:\/\/my\.telegram\.org\/apps/);
  assert.match(guide.body, /message box in Telegram Bridge.*\n!tg login qr\nTelegram Bridge replies.*QR code/u);
  assert.match(guide.formatted_body, /<pre><code>!tg login qr<\/code><\/pre>/u);
  assert.match(guide.body, /Settings → Devices → Link Desktop Device/u);
  assert.match(guide.body, /Approve and send/);
  assert.match(guide.body, /"api_hash": "REPLACE_WITH_YOUR_API_HASH"/);
  assert.ok(guide.body.includes(`${command.slice('sudo '.length)} telegram activate`));
  assert.match(guide.formatted_body, /&lt;login-id&gt;/);
  await reconcileTelegramPin(matrix, telegramState, command);
  assert.equal(messages.size, 3);
  assert.deepEqual(pinned, ['$telegram-operator', telegramState.entries['telegram-setup'].event]);
});
