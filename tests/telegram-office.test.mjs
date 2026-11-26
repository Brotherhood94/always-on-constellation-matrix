import assert from 'node:assert/strict';
import { mkdtemp, rm, stat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { requestTelegramOffice, telegramOfficeCommand } from '../agents/connector/telegram-office.mjs';
import { listenTelegramOffice, telegramOfficeAction } from '../deploy/matrix/bot/telegram-office.mjs';
import { addressedRequest, telegramRequest } from '../integrations/telegram/control.mjs';

const owner = '@operator:sample.test';
const agent = '@telegram-agent:sample.test';
const id = '@task-manager:sample.test';
const text = 'See you tomorrow!\nBring your notes.  ';

test('Office understands contact and group operations and preserves draft text', () => {
  const parse = message => telegramOfficeCommand(message, 'Task Manager', id);
  assert.deepEqual(parse('@Task Manager telegram add @sample'), { action: 'add', target: '@sample' });
  assert.deepEqual(parse(id + ': telegram add channel:12345'), { action: 'add', target: 'channel:12345' });
  assert.deepEqual(parse('telegram remove "Sample Group"'), { action: 'remove', target: 'Sample Group' });
  assert.deepEqual(parse('telegram draft group "Sample Group": ' + text), {
    action: 'draft', target: 'Sample Group', type: 'group', text,
  });
  assert.deepEqual(parse('telegram draft bad request'), { action: 'help' });
  assert.equal(parse('Please summarize telegram operations.'), null);
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
