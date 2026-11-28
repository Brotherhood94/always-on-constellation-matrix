import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createTelegramAdmin } from '../integrations/telegram/admin.mjs';
import { controlCommand, pollStart, TelegramStore } from '../integrations/telegram/policy.mjs';
import { telegramRestartProposal, telegramRestartRequest, telegramRestartSource } from '../integrations/root/protocol.mjs';
import { telegramIntent } from '../agents/connector/telegram-intent.mjs';

const owner = '@owner:fixture.test';
const dot = '@dot:fixture.test';
const agent = '@telegram-agent:fixture.test';
const architect = '@the-architect:fixture.test';
const portal = '!sample:fixture.test';
const settings = { owner, dot, agent, root: architect, domain: 'fixture.test', controlRoom: '!control:fixture.test',
  controlAudience: [owner, dot, agent, architect] };

function fixture(t) {
  const store = new TelegramStore(':memory:');
  const events = new Map();
  const calls = [];
  const receipts = [];
  let time = 1000;
  let ready = true;
  t.after(() => store.db.close());
  const matrix = {
    verifyControl: async () => ready,
    encryptEvent: async (room, type, content) => {
      assert.equal(room, settings.controlRoom);
      return { type, content };
    },
    put: async (_room, id, ciphertext) => {
      const event_id = '$' + id;
      if (!events.has(event_id)) events.set(event_id, { ...ciphertext, event_id, sender: agent, origin_server_ts: ++time });
      return event_id;
    },
    officeEvent: async (_room, event) => events.get(event),
  };
  let guide = async input => {
    if (input.action === 'admin-account') return { ok: true, login: '12345', revision: '8' };
    if (input.action === 'admin-chat') return { ok: true, room: portal, name: 'Sample', peer: 'user:12345' };
    return { ok: true, message: 'Confirmed in the fixture.' };
  };
  const admin = () => createTelegramAdmin({ store, settings, matrix, now: () => time,
    guide: async input => { calls.push(input); return guide(input); },
    record: (id, result) => receipts.push({ id, ...result }) });
  function source(id, action, sender = owner, additional = {}) {
    const content = { msgtype: 'm.text', body: '@Telegram Agent: ' + action, 'm.mentions': { user_ids: [agent] }, ...additional };
    events.set(id, { type: 'm.room.message', content, event_id: id, sender, origin_server_ts: ++time });
    return { id, thread: id };
  }
  function vote(poll, choice, sender = owner) {
    const event_id = '$vote-' + ++time;
    const type = 'org.matrix.msc3381.poll.response';
    const content = { [type]: { answers: [choice] }, 'm.relates_to': { rel_type: 'm.reference', event_id: poll } };
    const event = { type, content, event_id, sender, origin_server_ts: time };
    events.set(event_id, event);
    return [controlCommand({ ...event, room: settings.controlRoom, encrypted: true }, settings), event];
  }
  return { store, sources: events, calls, receipts, admin, source, vote,
    pollOf: id => store.get('SELECT * FROM telegram_admin_review WHERE id=?', id)?.poll,
    answers: poll => events.get(poll).content[pollStart].answers.map(item => item.id),
    guide: replacement => { guide = replacement; }, ready: value => { ready = value; }, advance: ms => { time += ms; } };
}

test('Telegram administration binds the first operator decision to the exact proposal and runs it once', async t => {
  const f = fixture(t);
  const relay = f.admin();
  const request = f.source('$admin-disconnect', 'disconnect account');
  await relay.request(request, { action: 'disconnect' });
  await relay.request(request, { action: 'disconnect' });
  await relay.tick();
  const poll = f.pollOf(request.id);
  const [accept, decline] = f.answers(poll);
  const [unauthorized, dotVote] = f.vote(poll, accept, dot);
  assert.equal(unauthorized, null);
  assert.equal(await relay.accept({ action: 'review', poll, choice: accept }, dotVote), false);
  await relay.accept(...f.vote(poll, 'f'.repeat(64)));
  assert.equal(f.calls.length, 1);
  await relay.accept(...f.vote(poll, accept));
  await relay.accept(...f.vote(poll, decline));
  await relay.tick();
  await f.admin().tick();
  assert.equal(f.calls.length, 2);
  assert.deepEqual(f.calls[1], { id: request.id, action: 'admin-disconnect', login: '12345', revision: '8',
    target: undefined, peer: undefined });
  assert.equal(f.receipts[0].status, 'completed');
});

test('a declined or expired administration poll never executes an operation', async t => {
  for (const expired of [false, true]) {
    const f = fixture(t);
    const relay = f.admin();
    const request = f.source('$admin-resync', 'resync');
    await relay.request(request, { action: 'resync' });
    await relay.tick();
    const poll = f.pollOf(request.id);
    const [accept, decline] = f.answers(poll);
    if (expired) f.advance(16 * 60_000);
    await relay.accept(...f.vote(poll, expired ? accept : decline));
    await relay.tick();
    assert.equal(f.calls.length, 1);
    assert.equal(f.receipts[0].status, expired ? 'expired' : 'declined');
  }
});

test('a redacted approval blocks execution', async t => {
  const f = fixture(t);
  const relay = f.admin();
  const request = f.source('$admin-revoke', 'revoke access to Sample');
  await relay.request(request, { action: 'revoke', target: 'Sample' });
  await relay.tick();
  const poll = f.pollOf(request.id);
  const ballot = f.vote(poll, f.answers(poll)[0]);
  await relay.accept(...ballot);
  f.sources.delete(ballot[1].event_id);
  await relay.tick();
  assert.equal(f.calls.length, 1);
  assert.equal(f.receipts[0].status, 'blocked');
});

test('a change to the reviewed chat binding requires a new vote', async t => {
  const f = fixture(t);
  const relay = f.admin();
  const request = f.source('$admin-binding-change', 'revoke access to Sample');
  const binding = { id: 'sample', room: portal, peer: 'user:12345', login: '12345', receiver: '12345',
    operator: owner, audience: 'a'.repeat(64), trigger: 'request', mode: 'draft' };
  f.store.bind(binding, settings.controlRoom, 1000);
  await relay.request(request, { action: 'revoke', target: 'Sample' });
  await relay.tick();
  const poll = f.pollOf(request.id);
  await relay.accept(...f.vote(poll, f.answers(poll)[0]));
  f.store.bind({ ...binding, mode: 'manual' }, settings.controlRoom, 1001);
  await relay.tick();
  assert.equal(f.calls.length, 1);
  assert.equal(f.receipts[0].status, 'blocked');
  assert.equal(f.store.binding(binding.id).active, 1);
});

test('an interrupted or unconfirmed administration request will not be repeated', async t => {
  const f = fixture(t);
  const relay = f.admin();
  const request = f.source('$admin-retry', 'disconnect account');
  await relay.request(request, { action: 'disconnect' });
  await relay.tick();
  const poll = f.pollOf(request.id);
  await relay.accept(...f.vote(poll, f.answers(poll)[0]));
  f.guide(() => { throw new Error('lost response'); });
  await relay.tick();
  await f.admin().tick();
  assert.equal(f.calls.length, 2);
  assert.equal(f.receipts[0].status, 'unknown');
});

test('Telegram Agent delegates a typed restart to The Architect for one approval', async t => {
  const f = fixture(t);
  const relay = f.admin();
  const request = f.source('$admin-restart', 'riavvia il bridge Telegram');
  const scope = telegramIntent(f.sources.get(request.id).content.body.split(': ')[1]);
  assert.deepEqual(scope, { action: 'restart' });
  await relay.request(request, scope);
  await relay.request(request, scope);
  assert.equal(f.sources.size, 2);
  assert.equal(f.pollOf(request.id), undefined);
  const handoff = [...f.sources.values()].at(-1);
  assert.equal(telegramRestartRequest(handoff, settings), request.id);
  assert.equal(telegramRestartSource(f.sources.get(request.id), settings), true);
  assert.equal(telegramRestartSource({ ...f.sources.get(request.id), sender: '@other:fixture.test' }, settings), false);
  assert.equal(telegramRestartSource({ ...f.sources.get(request.id), sender: dot }, settings), false);
  const typed = f.source('$dot-restart', 'restart bridge', dot, {
    'net.matrix_agents.control_request': { request_id: 'fixture-restart', telegram: { action: 'restart' } },
  });
  assert.equal(telegramRestartSource(f.sources.get(typed.id), settings), true);
  assert.deepEqual(JSON.parse(telegramRestartProposal().command), { kind: 'restart-service', target: 'telegram-bridge' });
});
