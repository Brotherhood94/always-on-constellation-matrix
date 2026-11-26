import { test } from 'node:test';
import assert from 'node:assert/strict';
import { freshness, historyWithin, httpsLink, officeCatalog } from '../dashboard/dashboard.mjs';
import { connectionDetails, connectionGraph, connectionRoute } from '../dashboard/connections.mjs';
import { reconcilePins, reconcileTelegramPin, shortcutRecipientKey } from '../deploy/matrix/bot/pins.mjs';

test('the dashboard shows the Telegram specialist and its managed route when Telegram is enabled', () => {
  const catalog = { contactPolicy: true, dynamicHandoffs: true,
    agents: [{ key: 'task-manager', name: 'Task Manager', role: 'coordinator', contacts: [] },
      { key: 'coder', name: 'Coder', role: 'worker', contacts: [] }] };
  assert.equal(officeCatalog(catalog, { services: {} }), catalog);
  const setup = officeCatalog(catalog, { services: { 'Telegram guide': 'running' } });
  assert.deepEqual(connectionGraph(setup).edges, [['task-manager', 'telegram-agent']]);
  assert.equal(setup.agents.at(-1).status, 'Setup required');
  assert.equal(officeCatalog(catalog, { services: { 'Telegram Agent': 'running' } }).agents.at(-1).status, 'Running');
  const deployed = officeCatalog(catalog, { time: 1810000000, services: { 'Telegram Control': 'running',
    'Telegram guide': 'running', 'Telegram engine': 'healthy' } }, {
    generatedAt: '2026-11-26T18:25:20Z', agent: { model: 'fixture-model', effort: 'low' },
    services: { connector: { state: 'running' } },
  });
  assert.equal(deployed.agents.at(-1).status, 'Available');
  assert.equal(deployed.agents.at(-1).model, 'fixture-model');
  assert.equal(deployed.agents.at(-1).healthCheckedAt, 1810000000);
  assert.deepEqual(catalog.agents[0].contacts, []);
});

test('Telegram setup only republishes unchanged instructions for a previously unseen device', async () => {
  let devices = ['desktop', 'phone'];
  let pinned = ['$operator'];
  let sends = 0;
  const state = { entries: {}, replaced: [] };
  const matrix = {
    recipientKey: async () => devices, getPinned: async () => pinned,
    send: async () => '$guide-' + ++sends,
    setPinned: async events => { pinned = events; }, save: async () => {},
  };
  for (const selected of [devices, ['desktop'], ['phone'], devices, [], ['desktop', 'new'], ['phone']]) {
    devices = selected;
    await reconcileTelegramPin(matrix, state, 'sudo ./constellation_matrix');
  }
  assert.equal(sends, 2);
  assert.deepEqual(pinned, ['$operator', '$guide-2']);
});

test('team connections use directional catalog permissions, including pending agents', () => {
  const catalog = {contactPolicy:true, dynamicHandoffs:true, agents:[
    {key:'coach', contacts:['scouter', 'removed'], status:'Available'},
    {key:'scouter', contacts:[], status:'Sign-in required'},
    {key:'observer', contacts:['coach'], role:'observer'},
  ]};
  const graph = connectionGraph(catalog);
  assert.deepEqual(graph.edges, [['coach', 'scouter']]);
  assert.deepEqual(graph.nodes.map(agent => agent.status), ['Available', 'Sign-in required']);
  assert.deepEqual(connectionGraph({...catalog, contactPolicy:false}).edges, []);
  assert.deepEqual(connectionGraph({...catalog, dynamicHandoffs:false}).edges, []);
});

test('a connection deep link filters both history and observed work by direction', () => {
  const straight = {sender:'@coach:fixture.test', recipient:'@scouter:fixture.test', status:'returned'};
  const reverse = {sender:straight.recipient, recipient:straight.sender, status:'blocked'};
  const allowed = {added:[['coach','scouter']], removed:[]};
  const other = {added:[], removed:[['scouter','coach']]};
  const report = {changes:[allowed,other], handoffs:[straight,reverse]};
  assert.deepEqual(connectionDetails(report, connectionRoute('#/connections/coach/scouter')),
    {changes:[allowed], handoffs:[straight]});
  assert.equal(connectionRoute('#/connections/coach/scouter/extra'), null);
});

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

  pinned = ['$telegram-operator', '$old-telegram-guide'];
  telegramState.entries['telegram-setup'] = { body: 'Set up Telegram', event: '$old-telegram-guide', revision: 'old' };
  await reconcileTelegramPin(matrix, telegramState, command);
  const guide = messages.get(telegramState.entries['telegram-setup'].event);
  assert.match(guide.body, /https:\/\/my\.telegram\.org\/apps/);
  assert.match(guide.body, /message box in Telegram Bridge.*\n!tg login qr\nTelegram Bridge replies.*QR code/u);
  assert.match(guide.formatted_body, /<pre><code>!tg login qr<\/code><\/pre>/u);
  assert.match(guide.body, /Settings → Devices → Link Desktop Device/u);
  assert.match(guide.body, /!tg <your Telegram two-step verification password>/u);
  assert.ok(guide.body.includes(`${command.slice('sudo '.length)} telegram setup`));
  assert.match(guide.body, /4\. Element · Ask Telegram Agent/u);
  assert.match(guide.body, /mention Telegram Agent in Agents → Office/u);
  assert.match(guide.formatted_body, /docs\/telegram\.md#start-the-drafting-agent">Finish Telegram Agent setup/u);
  for (const sample of ['add Sam Example', 'open the private chat with @sample_contact', 'with ID 1001',
    'add the Project Room group', 'prepare a private message', 'prepare a message for Project Room', 'summarize what I missed']) {
    assert.ok(guide.body.includes(sample));
  }
  assert.doesNotMatch(guide.body, /5\. Element|6\. Element|!tg (pm|search|resolve-identifier|create-portal)|telegram bind/u);
  assert.equal(guide['m.mentions'], undefined);
  assert.match(guide.body, /"api_hash": "REPLACE_WITH_YOUR_API_HASH"/);
  await reconcileTelegramPin(matrix, telegramState, command);
  assert.equal(messages.size, 3);
  assert.deepEqual(pinned, ['$telegram-operator', telegramState.entries['telegram-setup'].event]);
});
