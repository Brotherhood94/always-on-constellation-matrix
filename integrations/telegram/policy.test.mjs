import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { TelegramStore, controlCommand, reviewAnswer } from './policy.mjs';
import { addressedRequest, historyRange, resolveDestination, telegramRequest, updateReply } from './control.mjs';
import { verifyTelegramControlState } from './dot-telegram.mjs';

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

function review(store, draft, action = 'approve', poll = '$registered-review') {
  const row = store.draft(draft);
  store.registerReview(draft, row.version, poll);
  return { action: 'review', poll, choice: reviewAnswer(row, action) };
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

test('first-draft binding creation preserves existing bindings and operator pauses', () => {
  const store = fixture({ bindings: [] });
  const request = { createOnly: true };
  const first = { ...dm, trigger: 'request' };
  assert.equal(store.bind(first, config.controlRoom, now, request), true);
  assert.equal(store.record(input(first), now + 2), true);
  assert.equal(store.nextWork(now + 3), undefined);
  store.pause(first.id);
  assert.equal(store.bind(first, config.controlRoom, now + 4, request), false);
  assert.equal(store.bind({ ...first, id: 'different' }, config.controlRoom, now + 5, request), false);
  assert.equal(store.binding(first.id).active, 0);
  assert.equal(store.setting('pause_reason:' + first.id), 'operator_paused');
  store.pause();
  assert.equal(store.bind(group, config.controlRoom, now + 6, request), false);
  assert.equal(store.binding(group.id), undefined);
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
  assert.equal(controlCommand(valid, config), null);
  assert.equal(controlCommand({ ...valid, room: dm.room }, config), null);
  assert.equal(controlCommand({ ...valid, encrypted: false }, config), null);
  assert.equal(controlCommand({ ...valid, sender: '@telegram_22:test' }, config), null);
  assert.equal(controlCommand({ ...valid, content: { ...valid.content, body: `> ${valid.content.body}` } }, config), null);
  assert.equal(controlCommand({ ...valid, content: { ...valid.content, 'm.relates_to': { rel_type: 'm.replace' } } }, config), null);
  assert.equal(store.nextSend(now + 4), undefined);
  assert.equal(cmd(store, { ...command, version: 2 }, '$wrong-version'), false);
  assert.equal(cmd(store, command, '$text-command'), false);
  const vote = review(store, draft);
  assert.equal(cmd(store, vote), true);
  assert.equal(cmd(store, vote), false);
  assert.equal(cmd(store, vote, '$duplicate'), false);
  assert.equal(store.prepareSend(draft, '{"device":"stable"}', now + 6), true);
  assert.equal(store.prepareSend(draft, '{"device":"again"}', now + 7), false);
});

test('only one encrypted decision on a registered review poll can act on its exact draft', () => {
  const store = fixture();
  store.record(input(dm), now + 2);
  const id = candidate(store);
  const approve = reviewAnswer(store.draft(id), 'approve');
  const reject = reviewAnswer(store.draft(id), 'reject');
  const type = 'org.matrix.msc3381.poll.response';
  const vote = { room: config.controlRoom, sender: config.owner, type, encrypted: true, content: {
    'm.relates_to': { rel_type: 'm.reference', event_id: '$review' },
    [type]: { answers: [approve] },
  } };
  const command = controlCommand(vote, config);
  assert.deepEqual(command, { action: 'review', poll: '$review', choice: approve });
  assert.equal(controlCommand({ ...vote, encrypted: false }, config), null);
  assert.equal(controlCommand({ ...vote, room: dm.room }, config), null);
  assert.equal(controlCommand({ ...vote, sender: '@other:test' }, config), null);
  for (const answers of [[], [approve, reject], ['unknown']]) {
    assert.equal(controlCommand({ ...vote, content: { ...vote.content, [type]: { answers } } }, config), null);
  }
  store.registerReview(id, 2, '$wrong-version');
  assert.equal(cmd(store, { ...command, poll: '$wrong-version' }, '$vote0'), false);
  assert.equal(cmd(store, command, '$vote1'), false);
  store.registerReview(id, 1, '$review');
  store.registerReview(id, 1, '$other-poll');
  assert.equal(cmd(store, { ...command, poll: '$other-poll' }, '$vote2'), false);
  assert.equal(cmd(store, command, '$vote3'), true);
  assert.equal(cmd(store, { ...command, choice: reject }, '$vote4'), false);
  assert.equal(store.nextSend(now + 5).id, id);
  assert.equal(store.pendingReviewClose().event, '$review');
  store.closeReview('$review');
  assert.equal(store.pendingReviewClose(), undefined);

  store.record(input(dm, '$new'), now + 5);
  const freshWork = store.nextWork(now + 6);
  const fresh = store.saveCandidate(freshWork, 'Replacement', now + 7);
  store.registerReview(fresh, 1, '$fresh-review');
  assert.equal(cmd(store, command, '$vote5', now + 8), false);
  assert.equal(cmd(store, { ...command, poll: '$fresh-review' }, '$vote6', now + 8), false);
  const rejectFresh = reviewAnswer(store.draft(fresh), 'reject');
  assert.equal(cmd(store, { ...command, poll: '$fresh-review', choice: rejectFresh }, '$vote7', now + 8), true);
  assert.equal(store.draft(fresh).state, 'cancelled');
  assert.equal(store.nextSend(now + 8), undefined);
});

test('Bridge cutover reissues approved-but-unsent drafts with a fresh poll exactly once', () => {
  const store = fixture();
  store.run("UPDATE binding SET mode='auto' WHERE id=?", dm.id);
  store.record(input(dm), now + 2);
  const id = candidate(store);
  const old = review(store, id);
  assert.equal(cmd(store, old), true);
  store.run('UPDATE draft SET control_event=? WHERE id=?', '$old-control', id);
  const before = store.draft(id);
  const anchors = new Map([['$old-control', '$carried-draft']]);
  store.migrateReviews('!bridge:test', config.controlRoom, anchors, now + 5);
  const after = store.draft(id);
  assert.equal(store.binding(dm.id).mode, 'draft');
  assert.equal(after.state, 'awaiting_approval');
  assert.equal(after.control_event, '$carried-draft');
  assert.equal(after.body, before.body);
  assert.equal(after.txn, before.txn);
  assert.equal(after.version, 2);
  assert.equal(after.approval, null);
  assert.equal(cmd(store, old, '$late-vote'), false);
  store.migrateReviews('!bridge:test', config.controlRoom, anchors, now + 6);
  assert.equal(store.draft(id).version, 2);
  const cfg = { ...config, controlRoom: '!bridge:test' };
  const event = { room: cfg.controlRoom, sender: config.owner, encrypted: true, event: '$new-vote' };
  assert.equal(store.command(event, review(store, id, 'approve', '$fresh-poll'), cfg, now + 7), true);
  assert.equal(store.prepareSend(id, '{}', now + 8), true);
  assert.throws(() => store.migrateReviews('!other:test', cfg.controlRoom, new Map(), now + 9), /Reconcile/);
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
    assert.equal(cmd(store, review(store, draft)), true);
    invalidate(store);
    assert.equal(store.prepareSend(draft, '{}', now + 7), false);
    assert.equal(store.binding('group').active, 1);
  }
});

test('poll approval goes through the outbox and 429 retry preserves the encrypted body and transaction', () => {
  const store = fixture({ bindings: [dm] });
  store.record(input(dm), now + 2);
  const id = candidate(store);
  assert.equal(cmd(store, review(store, id)), true);
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
    let store = fixture({ path, bindings: [dm, group] });
    store.record(input(dm), now + 2);
    const id = candidate(store);
    const first = store.draft(id);
    assert.equal(cmd(store, review(store, id)), true);
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

test('Dot can submit a real mention and never act on a draft approval', () => {
  const settings = { ...config, dot: '@dot:test', agent: '@telegram-agent:test' };
  const message = { sender: settings.dot, type: 'm.room.message', event_id: '$dot-1', content: {
    msgtype: 'm.text', body: '@telegram-agent: Prepare a message.',
    'm.mentions': { user_ids: [settings.agent] },
    [telegramRequest]: { action: 'compose', destination: 'group', chat_type: 'group', text: 'Exact message' },
  } };
  assert.equal(addressedRequest(message, settings).text, 'Exact message');
  assert.equal(addressedRequest({ ...message, sender: '@unrelated:test' }, settings), null);
  assert.equal(addressedRequest({ ...message, content: { ...message.content, 'm.mentions': {} } }, settings), null);
  assert.equal(addressedRequest({ ...message, content: { ...message.content, 'm.relates_to': { rel_type: 'm.replace' } } }, settings), null);
  const store = fixture();
  const request = addressedRequest(message, settings);
  assert.equal(store.queueControl(message.event_id, request, now), true);
  assert.equal(store.queueControl(message.event_id, request, now), false);
  const queued = store.nextControl('queued', now);
  const binding = store.binding('group');
  assert.equal(store.prepareControlDraft(queued, binding, 'Project Room (group)', request.text, '$wrong-room', now), false);
  assert.equal(store.prepareControlDraft(queued, binding, 'Project Room (group)', request.text, null, now), true);
  assert.equal(store.prepareControlDraft(queued, binding, 'Project Room (group)', request.text, null, now), false);
  const draft = store.pendingNotice();
  assert.equal(draft.state, 'awaiting_approval');
  assert.equal(draft.body, request.text);
  assert.equal(store.nextSend(now), undefined);
  const approve = review(store, draft.id);
  assert.equal(store.command({ sender: settings.dot, room: config.controlRoom, event: '$dot-approval', encrypted: true },
    approve, settings, now), false);
  assert.equal(cmd(store, approve), true);
  assert.equal(store.prepareSend(draft.id, '{}', now + 6), true);
});

test('updates require a single connected chat and mention both people in the request thread', () => {
  const store = fixture();
  const destinations = [{ name: 'Sam Example', binding: store.binding('first') },
    { name: 'Progetto Energia', binding: store.binding('group') }];
  assert.equal(resolveDestination({ question: 'Aggiornami su Sam' }, destinations), null);
  assert.equal(resolveDestination({ question: 'Cosa ha detto Sam nella chat privata?' }, destinations).binding.id, 'first');
  assert.equal(resolveDestination({ question: 'Novità su Progetto Energia oggi?' }, destinations).binding.id, 'group');
  assert.equal(resolveDestination({ destination: 'Sam Example', chatType: 'group' }, destinations), null);
  assert.equal(resolveDestination({ destination: 'Sam Example', chatType: 'direct' }, destinations).binding.id, 'first');
  const range = historyRange('cosa è successo oggi', Date.parse('2026-11-23T23:30:00+01:00'));
  assert.equal(range.includes(Date.parse('2026-11-22T23:00:00+01:00')), false);
  assert.equal(range.includes(Date.parse('2026-11-23T00:00:00+01:00')), true);
  const settings = { ...config, dot: '@dot:test', agent: '@telegram-agent:test' };
  const reply = updateReply(settings, '$request', 'Message <with markup>', [{ label: '[1] Source', url: 'https://matrix.to/#/room/event' }]);
  assert.deepEqual(reply['m.mentions'].user_ids, [settings.owner, settings.dot]);
  assert.equal(reply['m.relates_to'].event_id, '$request');
  assert.match(reply.formatted_body, /&lt;with markup&gt;/);
  assert.match(reply.formatted_body, /href="https:\/\/matrix.to\/#\/room\/event"/);
});

test('Dot checks the exact encrypted Control audience before posting', () => {
  const members = [config.owner, '@dot:test', '@telegram-agent:test'];
  const state = [
    { type: 'm.room.create', state_key: '', content: { 'm.federate': false } },
    { type: 'm.room.join_rules', state_key: '', content: { join_rule: 'invite' } },
    { type: 'm.room.encryption', state_key: '', content: { algorithm: 'm.megolm.v1.aes-sha2' } },
    ...members.map(state_key => ({ type: 'm.room.member', state_key, content: { membership: 'join' } })),
  ];
  assert.equal(verifyTelegramControlState(state, { members }), true);
  assert.equal(verifyTelegramControlState(state.slice(0, -1), { members }), false);
  assert.equal(verifyTelegramControlState([...state, { type: 'm.room.member', state_key: '@unexpected:test',
    content: { membership: 'invite' } }], { members }), false);
});

test('an operator can explicitly reply or ask a follow-up in the same thread', () => {
  const settings = { ...config, dot: '@dot:test', agent: '@telegram-agent:test' };
  const request = { sender: settings.owner, type: 'm.room.message', event_id: '$follow-up', content: {
    msgtype: 'm.text', body: '@telegram-agent: rispondi',
    'm.mentions': { user_ids: [settings.agent] },
    'm.relates_to': { rel_type: 'm.thread', event_id: '$root', 'm.in_reply_to': { event_id: '$agent-update' } },
  } };
  assert.deepEqual(addressedRequest(request, settings), { action: 'reply', question: 'rispondi', thread: '$root' });
  assert.deepEqual(addressedRequest({ ...request, content: { ...request.content, body: '@telegram-agent: cosa ha detto prima?' } }, settings),
    { action: 'update', question: 'cosa ha detto prima?', thread: '$root' });
  assert.equal(addressedRequest({ ...request, content: { ...request.content, 'm.relates_to': { rel_type: 'm.replace', event_id: '$root' } } }, settings),
    null);
  assert.equal(addressedRequest({ ...request, content: { ...request.content, body: '> @telegram-agent: rispondi\n\n', } }, settings), null);

  const reply = updateReply(settings, '$follow-up', 'Follow-up result', [], '$root');
  assert.equal(reply['m.relates_to'].event_id, '$root');
  assert.equal(reply['m.relates_to']['m.in_reply_to'].event_id, '$follow-up');
});

test('an explicit operator send uses the outbox without a poll, preserving the verified target and exact body', () => {
  const store = fixture();
  const request = { event: '$operator-send' };
  assert.equal(store.queueControl(request.event, { action: 'compose' }, now, { requireDot: false }), true);
  assert.equal(store.prepareControlDraft(request, store.binding(dm.id), 'Verified recipient', 'exact\ntext', null, now + 1, undefined, '@other:test'), false);
  assert.equal(store.prepareControlDraft(request, store.binding(dm.id), 'Verified recipient', 'exact\ntext', null, now + 1, undefined, config.owner), true);
  const send = store.nextSend(now + 2);
  assert.equal(send.body, 'exact\ntext');
  assert.equal(store.all('SELECT event FROM draft_review').length, 0);
  assert.equal(store.record(input(dm), now + 2), true);
  assert.equal(store.prepareSend(send.id, 'ciphertext', now + 3), true);
  assert.equal(store.prepareSend(send.id, 'ciphertext', now + 3), false);
});
