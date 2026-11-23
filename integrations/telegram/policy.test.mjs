import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { TelegramStore, controlCommand } from './policy.mjs';

const now = 2_000_000;
const config = { controlRoom: '!control:test', owner: '@test:test' };
const dm = { id: 'first', login: '11', receiver: '11', peer: 'user:22', room: '!dm:test',
  audience: 'a'.repeat(64), operator: config.owner, mode: 'draft', trigger: 'inbound' };
const group = { ...dm, id: 'group', peer: 'channel:33', room: '!group:test', trigger: 'request' };
const input = (binding, event = '$remote', author = 'remote', body = 'Independent test message') => ({
  room: binding.room, event, author, body, ts: now + 1,
});

function fixture(options = {}) {
  const store = new TelegramStore(options.path || ':memory:');
  for (const binding of options.bindings ?? [dm, group]) store.bind(binding, config.controlRoom, now);
  return store;
}

function candidate(store) {
  const work = store.nextWork(now + 3);
  return store.saveCandidate(work, 'Proposal', now + 4);
}

function cmd(store, command, event = '$control', time = now + 5) {
  return store.command({ room: config.controlRoom, sender: config.owner, event, encrypted: true }, command, config, time);
}

test('manual never queues, forum topics and group chatter are excluded, and scope is per binding', () => {
  const store = fixture({ bindings: [{ ...dm, mode: 'manual' }, group] });
  assert.throws(() => store.bind({ ...group, peer: 'channel:33:4' }, config.controlRoom, now));
  assert.throws(() => store.bind({ ...group, trigger: 'inbound' }, config.controlRoom, now));
  assert.throws(() => store.bind({ ...group, id: 'other', room: dm.room }, config.controlRoom, now));
  assert.equal(store.record(input({ room: '!unrelated:test' }), now + 2), false);
  assert.equal(store.record(input(dm, '$dm'), now + 2), true);
  assert.equal(store.record(input(group, '$group'), now + 2), true);
  assert.equal(store.nextWork(now + 3), undefined);
  assert.equal(cmd(store, { action: 'draft', binding: 'group', source: '$dm' }), false);
  assert.equal(cmd(store, { action: 'draft', binding: 'group', source: '$group' }, '$control2'), true);
  const work = store.nextWork(now + 6);
  assert.equal(work.binding, 'group');
  assert.deepEqual(store.history(work).map(row => row.event), ['$group']);
});

test('owner echo is context, old activity and repeated Matrix events do not trigger duplicates', () => {
  const store = fixture();
  assert.equal(store.record({ ...input(dm, '$older'), ts: now - 5 }, now + 2), false);
  assert.equal(store.record(input(dm, '$echo', 'owner'), now + 2), true);
  assert.equal(store.nextWork(now + 3), undefined);
  assert.equal(store.record(input(dm), now + 2), true);
  assert.equal(store.record(input(dm), now + 2), false);
  assert.equal(store.nextWork(now + 3).source, '$remote');
  assert.equal(store.nextWork(now + 3), undefined);
});

test('only the encrypted private control event can authorize a particular draft', () => {
  const store = fixture();
  store.record(input(dm), now + 2);
  const draft = candidate(store);
  const command = { action: 'approve', draft, version: 1 };
  const valid = { room: config.controlRoom, sender: config.owner, encrypted: true,
    content: { msgtype: 'm.text', body: `telegram approve ${draft} 1` } };
  assert.deepEqual(controlCommand(valid, config), command);
  assert.equal(controlCommand({ ...valid, room: dm.room }, config), null);
  assert.equal(controlCommand({ ...valid, encrypted: false }, config), null);
  assert.equal(controlCommand({ ...valid, sender: '@telegram_22:test' }, config), null);
  assert.equal(controlCommand({ ...valid, content: { ...valid.content, body: `> ${valid.content.body}` } }, config), null);
  assert.equal(controlCommand({ ...valid, content: { ...valid.content, 'm.relates_to': { rel_type: 'm.replace' } } }, config), null);
  assert.equal(store.nextSend(now + 4), undefined);
  assert.equal(cmd(store, { ...command, version: 2 }, '$wrong-version'), false);
  assert.equal(cmd(store, command), true);
  assert.equal(cmd(store, command), false);
  assert.equal(cmd(store, command, '$duplicate'), false);
  assert.equal(store.prepareSend(draft, '{"device":"stable"}', now + 6), true);
  assert.equal(store.prepareSend(draft, '{"device":"again"}', now + 7), false);
});

test('new human content, policy revision, expiry and pause protect against stale sending', () => {
  for (const invalidate of [
    store => store.record(input(dm, '$human', 'owner'), now + 6),
    store => store.bind({ ...dm, mode: 'manual' }, config.controlRoom, now + 6),
    store => store.expire(now + 1_800_010),
    store => store.pause('first'),
  ]) {
    const store = fixture();
    store.record(input(dm), now + 2);
    const draft = candidate(store);
    assert.equal(cmd(store, { action: 'approve', draft, version: 1 }), true);
    invalidate(store);
    assert.equal(store.prepareSend(draft, '{}', now + 7), false);
    assert.equal(store.binding('group').active, 1);
  }
});

test('explicit auto goes through the outbox and 429 retry preserves the encrypted body and transaction', () => {
  const store = fixture({ bindings: [{ ...dm, mode: 'auto' }] });
  store.record(input(dm), now + 2);
  const id = candidate(store);
  const draft = store.nextSend(now + 5);
  assert.equal(draft.id, id);
  assert.equal(store.prepareSend(id, '{"body":"cipher"}', now + 6), true);
  store.retry(id, now + 8);
  assert.equal(store.nextSend(now + 9), undefined);
  const retry = store.nextSend(now + 30_008);
  assert.equal(retry.ciphertext, '{"body":"cipher"}');
  assert.equal(retry.txn, draft.txn);
  assert.throws(() => store.prepareSend(id, '{"body":"other"}', now + 30_009));
  assert.equal(store.prepareSend(id, retry.ciphertext, now + 30_009), true);
});

test('uncertain post survives restart, receipt must match and it never blindly sends again', () => {
  const temporary = mkdtempSync(join(tmpdir(), 'telegram-policy-'));
  try {
    const path = join(temporary, 'state.sqlite3');
    let store = fixture({ path, bindings: [{ ...dm, mode: 'auto' }, group] });
    store.record(input(dm), now + 2);
    const id = candidate(store);
    const first = store.draft(id);
    assert.equal(store.prepareSend(id, '{}', now + 6), true);
    store.db.close();
    store = new TelegramStore(path);
    store.recover(now + 10);
    assert.equal(store.draft(id).state, 'unknown');
    assert.equal(store.nextSend(now + 10), undefined);
    store.delivery(group.room, '$other', 'SUCCESS', now + 11);
    store.delivery(dm.room, '$first', 'SUCCESS', now + 11);
    store.accepted(first.txn, group.room, '$first');
    assert.equal(store.draft(id).state, 'unknown');
    store.accepted(first.txn, dm.room, '$first');
    assert.equal(store.draft(id).state, 'bridge_confirmed');
    store.db.close();
  } finally {
    rmSync(temporary, { recursive: true, force: true });
  }
});

test('interrupted model work pauses only its binding and rotation prevents session reuse', () => {
  const store = fixture();
  store.record(input(dm), now + 2);
  const work = store.nextWork(now + 3);
  store.session(work, 'existing-session', 'engine-01');
  assert.throws(() => store.session(work, 'other-session', 'engine-01'));
  store.recover(now + 4);
  assert.equal(store.binding(dm.id).active, 0);
  assert.equal(store.binding(group.id).active, 1);
  store.reset(dm, config.controlRoom, now + 5);
  assert.equal(store.binding(dm.id).session, null);
  assert.equal(store.binding(dm.id).generation, 2);
});
