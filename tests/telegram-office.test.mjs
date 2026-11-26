import assert from 'node:assert/strict';
import { mkdtemp, rm, stat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { requestTelegramOffice, telegramOfficeCommand } from '../agents/connector/telegram-office.mjs';
import { acceptedMessage } from '../agents/connector/matrix-routing.mjs';
import { listenTelegramOffice, telegramControlSearch, telegramOfficeAction } from '../deploy/matrix/bot/telegram-office.mjs';
import { addressedRequest, telegramRequest } from '../integrations/telegram/control.mjs';

const owner = '@operator:sample.test';
const agent = '@telegram-agent:sample.test';
const id = '@task-manager:sample.test';
const text = 'See you tomorrow!\nBring your notes.  ';

test('Office understands contact and group operations and preserves draft text', () => {
  const parse = message => telegramOfficeCommand(message, 'Task Manager', id);
  assert.deepEqual(parse('@Task Manager telegram add @sample'), { action: 'add', target: '@sample' });
  assert.deepEqual(parse('@Task Manager telegram add user:1001'), { action: 'add', target: 'user:1001' });
  assert.deepEqual(parse(id + ': telegram add channel:12345'), { action: 'add', target: 'channel:12345' });
  assert.deepEqual(parse('telegram remove "Sample Group"'), { action: 'remove', target: 'Sample Group' });
  assert.deepEqual(parse('telegram draft group "Sample Group": ' + text), {
    action: 'draft', target: 'Sample Group', type: 'group', text,
  });
  assert.deepEqual(parse('telegram draft bad request'), { action: 'help' });
  assert.equal(parse('Please summarize telegram operations.'), null);
});

test('a real Office mention opens a Telegram contact from the Italian request', () => {
  const room = '!office:sample.test';
  const request = acceptedMessage({
    room_id: room, sender: owner, event_id: '$request', origin_server_ts: 10, type: 'm.room.message',
    content: {
      msgtype: 'm.text', body: 'Task Manager: aggiungi @sample_contact alle room nel teelgram space',
      formatted_body: '<a href="https://matrix.to/#/' + encodeURIComponent(id) +
        '">Task Manager</a>: aggiungi @sample_contact alle room nel teelgram space',
      'm.mentions': { user_ids: [id] },
    },
  }, { roomId: room, owner, userId: id, agents: [id], activatedAt: 0,
    displayNames: { [id]: 'Task Manager' }, routingPolicy: { coordinator: id, dynamic: true } });
  assert.deepEqual(telegramOfficeCommand(request.body, 'Task Manager', id), { action: 'add', target: '@sample_contact' });
});

test('contact requests require one Telegram username and a standalone Telegram action', () => {
  const parse = message => telegramOfficeCommand(message, 'Task Manager', id);
  for (const message of [
    'Aggiungi @sample_contact alle stanze di Telegram.',
    'apri la chat con @sample_contact su Telegram',
    'Add @sample_contact to Telegram Rooms',
    'Open a chat with @sample_contact on Telegram.',
  ]) assert.deepEqual(parse(message), { action: 'add', target: '@sample_contact' });
  for (const message of [
    'Aggiungi @sample_contact a tutte le room del Telegram Space',
    'Add @sample_contact to the Telegram room',
    'Invite @sample_contact into all Telegram rooms',
    'Non aggiungere @sample_contact alle room di Telegram',
    'Aggiungi @sample_contact:example.org alle room di Telegram',
    'Aggiungi @sample_contact e @another alle room di Telegram',
    'Aggiungi @sample_contact alle room di Telegram e mandagli un messaggio',
    '`Add @sample_contact to Telegram Rooms`',
  ]) assert.equal(parse(message), null);
});

test('the operator and the Telegram Guide can request exact drafts without Dot', () => {
  const settings = { owner, agent };
  const direct = { event_id: '$operator-request', sender: owner, type: 'm.room.message',
    content: { msgtype: 'm.text', body: 'telegram draft group "Sample Group": ' + text } };
  assert.deepEqual(addressedRequest(direct, settings), {
    action: 'compose', destination: 'Sample Group', chatType: 'group', text,
  });
  assert.equal(addressedRequest({ ...direct, sender: id }, settings), null);
  const relay = { ...direct, sender: agent, content: { msgtype: 'm.text', body: 'Draft from Office.',
    [telegramRequest]: { origin: 'office', action: 'compose', destination: 'Sample', chat_type: 'direct', text } } };
  assert.equal(addressedRequest(relay, settings).text, text);
  const dot = '@dot:sample.test';
  assert.equal(addressedRequest({ ...relay, sender: dot, content: { ...relay.content, 'm.mentions': {user_ids:[agent]} } },
    { ...settings, dot }), null);
  assert.equal(addressedRequest({ ...relay, content: { ...relay.content, 'm.new_content': {} } }, settings), null);
});

test('the private action socket is restricted and refuses malformed requests', async () => {
  const folder = await mkdtemp(join(tmpdir(), 'cmo-'));
  const socket = join(folder, 'actions.sock');
  let server;
  try {
    server = await listenTelegramOffice(socket, async () => ({}));
    assert.equal((await stat(socket)).mode & 0o777, 0o600);
    assert.equal((await requestTelegramOffice(socket, '$test', { action: 'remove', target: '\nother' })).ok, false);
    assert.equal((await requestTelegramOffice(socket, '$test', { action: 'approve', target: 'Sample' })).ok, false);
  } finally {
    if (server) await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
    await rm(folder, { recursive: true, force: true });
  }
});

test('an unexpected Control member blocks the Office draft before encryption', async () => {
  const make = (type, content, state_key = '') => ({ type, state_key, content });
  const client = { getRoomState: async () => [
    make('m.room.create', { 'm.federate': false }), make('m.room.join_rules', { join_rule: 'invite' }),
    make('m.room.encryption', { algorithm: 'm.megolm.v1.aes-sha2' }),
    make('m.room.member', { membership: 'join' }, owner), make('m.room.member', { membership: 'join' }, agent),
    make('m.room.member', { membership: 'invite' }, id),
  ] };
  const result = await telegramOfficeAction({ id: '$test', action: 'draft', type: 'direct', target: 'Sample', text },
    { client, owner, guide: agent, domain: 'sample.test', control: '!control:sample.test' });
  assert.equal(result.ok, false);
});

test('Telegram Control publishes an encrypted, bounded reply to an operator search', async () => {
  const control = '!control:sample.test';
  const event = { event_id: '$search', type: 'm.room.message', sender: owner, origin_server_ts: Date.now(),
    content: { msgtype: 'm.text', body: '!tg search Sample Name' } };
  const state = [{ type: 'm.room.create', state_key: '', content: { 'm.federate': false } },
    { type: 'm.room.encryption', state_key: '', content: { algorithm: 'm.megolm.v1.aes-sha2' } },
    { type: 'm.room.join_rules', state_key: '', content: { join_rule: 'invite' } },
    ...[owner, agent].map(state_key => ({ type: 'm.room.member', state_key, content: { membership: 'join' } }))];
  const transactions = [];
  const bodies = [];
  let requests = 0;
  const client = { getRoomState: async () => state,
    crypto: { encryptRoomEvent: async (room, type, content) => {
      assert.equal(room, control);
      assert.equal(type, 'm.room.message');
      assert.deepEqual(content['m.relates_to'], { 'm.in_reply_to': { event_id: '$search' } });
      bodies.push(content.body);
      return { ciphertext: 'synthetic' };
    } },
    doRequest: async (method, route, query, payload) => {
      assert.equal(method, 'PUT');
      assert.match(route, /\/send\/m\.room\.encrypted\/telegram-search-[a-f0-9]{64}$/u);
      assert.deepEqual(payload, { ciphertext: 'synthetic' });
      transactions.push(route);
    },
  };
  const options = { client, control, owner, guide: agent, domain: 'sample.test', readKey: async () => 'secret',
    bridgeFetch: async (url, request) => {
      requests += 1;
      assert.equal(new URL(url).pathname, '/_matrix/provision/v3/search_users');
      assert.equal(new URL(url).searchParams.get('user_id'), owner);
      assert.equal(request.method, 'POST');
      assert.deepEqual(JSON.parse(request.body), { query: 'Sample Name' });
      return Response.json({ results: [{ id: '1001', name: 'Sample Person', identifiers: ['+12555550100'] },
        { id: '1001', name: 'Duplicate' }, ...Array.from({ length: 11 }, (_, id) => ({ id: String(2001 + id), name: 'Other' }))] });
    },
  };
  await telegramControlSearch('!office:sample.test', event, options);
  await telegramControlSearch(control, { ...event, sender: '@dot:sample.test' }, options);
  await telegramControlSearch(control, { ...event, content: { ...event.content, 'm.new_content': {} } }, options);
  await telegramControlSearch(control, { ...event, origin_server_ts: 0 }, options);
  assert.equal(requests, 0);
  await telegramControlSearch(control, event, options);
  await telegramControlSearch(control, event, options);
  assert.equal(requests, 2);
  assert.equal(new Set(transactions).size, 1);
  assert.match(bodies[0], /Sample Person.*1001/u);
  assert.doesNotMatch(bodies[0], /Duplicate|12555550100/u);
  assert.equal(bodies[0].split('\n').filter(line => line.startsWith('•')).length, 10);

  await telegramControlSearch(control, event, { ...options, bridgeFetch: async () =>
    Response.json({ errcode: 'FI.MAU.NOT_LOGGED_IN', error: 'do not echo this' }, { status: 400 }) });
  assert.match(bodies.at(-1), /!tg login qr/u);
  assert.doesNotMatch(bodies.at(-1), /do not echo/u);

  await telegramControlSearch(control, { ...event, content: { msgtype: 'm.text', body: '!tg search' } }, options);
  assert.match(bodies.at(-1), /followed by a name or Telegram username/u);
  await telegramControlSearch(control, event, { ...options, bridgeFetch: async () => Response.json({ results: [] }) });
  assert.match(bodies.at(-1), /Telegram returned no matches/u);

  state.push({ type: 'm.room.member', state_key: '@unexpected:sample.test', content: { membership: 'invite' } });
  await telegramControlSearch(control, event, options);
  assert.equal(requests, 2);
  assert.equal(bodies.length, 5);
});

test('Office reports bridge sign-in, unresolved usernames, and provisioning failures separately', async () => {
  const make = (type, content, state_key = '') => ({ type, state_key, content });
  const options = {
    client: { getRoomState: async () => [
      make('m.room.create', { 'm.federate': false, type: 'm.space' }),
      make('m.room.join_rules', { join_rule: 'invite' }),
      make('m.room.member', { membership: 'join' }, owner),
      make('m.room.member', { membership: 'join' }, agent),
    ] },
    database: { query: async () => ({ rows: [] }) },
    key: 'fixture', owner, guide: agent, domain: 'sample.test', space: '!telegram:sample.test',
  };
  for (const [status, code, error, expected] of [
    [400, 'FI.MAU.NOT_LOGGED_IN', 'Not logged in', /!tg login qr/u],
    [403, 'M_FORBIDDEN', 'Login is not logged in', /Telegram session is disconnected/u],
    [404, 'M_NOT_FOUND', 'Identifier not found', /could not resolve that exact username/u],
    [401, 'M_UNKNOWN_TOKEN', 'Invalid auth token', /telegram layout/u],
    [403, 'M_FORBIDDEN', 'Provisioning API is disabled', /telegram layout/u],
    [429, 'M_LIMIT_EXCEEDED', 'Too many requests', /rate-limited/u],
    [500, 'M_UNKNOWN', 'Database connection secret never print this', /HTTP 500/u],
  ]) {
    const result = await telegramOfficeAction({ id: '$test', action: 'add', target: '@sample' }, {
      ...options, bridgeFetch: async () => Response.json({ errcode: code, error }, { status }),
    });
    assert.equal(result.ok, false);
    assert.match(result.message, expected);
    assert.doesNotMatch(result.message, /never print this/u);
  }
  const unknownPeer = await telegramOfficeAction({ id: '$test-peer', action: 'add', target: 'user:1001' }, {
    ...options, bridgeFetch: async url => {
      assert.match(url, /\/create_dm\/1001\?/u);
      return Response.json({ errcode: 'M_NOT_FOUND' }, { status: 404 });
    },
  });
  assert.equal(unknownPeer.ok, false);
  assert.match(unknownPeer.message, /could not resolve that Telegram user ID.*!tg search/u);
  for (const target of ['1001', 'user:0', 'user:-1001', 'user:9999999999999999999']) {
    const invalidPeer = await telegramOfficeAction({ id: '$invalid-peer', action: 'add', target }, {
      ...options, bridgeFetch: async () => assert.fail(`Unqualified or invalid peer sent to bridge: ${target}`),
    });
    assert.equal(invalidPeer.ok, false);
    assert.match(invalidPeer.message, /user:/u);
  }
});
