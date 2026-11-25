import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import test from 'node:test';
import { choiceId, command, createRosterApproval, voteChoice, workerProfile } from './roster-approval.mjs';

const owner = '@operator:fixture.test';
const room = '!chief:fixture.test';
const reply = 'org.matrix.msc3381.poll.response';
const approval = (poll, choice) => ({ type: reply, content: {
  'm.relates_to': { rel_type: 'm.reference', event_id: poll }, [reply]: { answers: [choice] },
} });
const message = body => ({ type: 'm.room.message', content: { msgtype: 'm.text', body } });
const profile = workerProfile('fixture', JSON.stringify({
  name: 'Fixture Worker', model: 'gpt-5.4', effort: 'low', limits: 'Work on assigned Office tasks.',
  capabilities: [{ code: 'verify-artifact', description: 'Check an artifact.', resources: ['task:*'] }],
}));

test('roster commands are explicit and drafts only contain local worker profile fields', () => {
  assert.deepEqual(command(message('hire fixture Check artifacts')), { kind: 'add', key: 'fixture', brief: 'Check artifacts' });
  assert.equal(command(message('retire fixture extra')), null);
  assert.equal(command(message('retire ../fixture')), null);
  assert.equal(command(message(42)), null);
  assert.equal(command({ ...message('retire fixture'), content: {
    ...message('retire fixture').content, 'm.relates_to': { rel_type: 'm.replace' },
  } }), null);
  assert.equal(profile.profile.role, 'worker');
  assert.throws(() => workerProfile('fixture', '{"name":"bad model","model":"../fixture"}'));
});

test('poll choices bind the exact proposal and reject late, unrelated, or repeated votes', () => {
  const row = { request: '$request', proposal: '$proposal', operation: '{"kind":"retire","agent":"fixture"}',
    poll: '$poll', proposed: 100, polled: 101 };
  const accept = approval(row.poll, choiceId(row, 'approve'));
  assert.equal(voteChoice(accept, row, 102), 'approve');
  assert.equal(voteChoice(approval(row.poll, choiceId(row, 'reject')), row, 102), 'reject');
  assert.equal(voteChoice(accept, { ...row, operation: row.operation.replace('fixture', 'other') }, 102), null);
  assert.equal(voteChoice(approval('$other', choiceId(row, 'approve')), row, 102), null);
  assert.equal(voteChoice(accept, row, 100), null);
  assert.equal(voteChoice(accept, row, 900_101), null);
  assert.equal(voteChoice(accept, { ...row, action: 'reject' }, 102), null);
});

test('private approvals resume pending work and only invoke the exact accepted operation', async () => {
  const db = new DatabaseSync(':memory:');
  const sent = [];
  let time = 1000;
  const client = {
    crypto: { encryptRoomEvent: async (_room, type, content) => ({ type, content }) },
    doRequest: async (_method, _path, _qs, encrypted) => {
      sent.push(encrypted);
      return { event_id: '$fixture-' + sent.length };
    },
    getRawEvent: async () => ({ origin_server_ts: time }),
  };
  const envelopes = (id, sender = owner) => ({ type: 'm.room.encrypted', sender, room_id: room,
    event_id: '$' + id, origin_server_ts: time });
  const applied = [];
  const options = {
    db, client, room, owner, checkDestination: async () => {},
    now: () => time, draft: async () => profile,
    currentAgents: () => [{ key: 'existing', role: 'worker', placement: 'local' }],
    apply: async payload => {
      applied.push(payload);
      const operation = JSON.parse(payload.operation);
      return { status: operation.kind === 'add' ? 'provisioned' : applied.length === 2 ? 'draining' : 'retired' };
    },
  };
  let bridge = createRosterApproval(options);
  const find = id => db.prepare('SELECT * FROM roster_requests WHERE request=?').get('$' + id);
  assert.equal(await bridge.accept(message('hire fixture Check artifacts'), envelopes('forged', '@other:fixture.test')), null);
  assert.equal(await bridge.accept(message('retire manager'), envelopes('protected')), 'Choose an ordinary local Office worker.');
  await bridge.accept(message('hire fixture Check artifacts'), envelopes('hire'));
  await bridge.tick();
  const hire = find('hire');
  assert.ok(hire.proposal && hire.poll);
  assert.equal(applied.length, 0);
  time++;
  await bridge.accept(approval(hire.poll, choiceId(hire, 'approve')), envelopes('wrong-vote', '@other:fixture.test'));
  assert.equal(find('hire').action, null);
  await bridge.accept(approval(hire.poll, choiceId(hire, 'approve')), envelopes('accepted'));
  bridge = createRosterApproval(options);
  await bridge.tick();
  assert.equal(applied[0].operation, hire.operation);
  assert.ok(sent.at(-1).content.body.includes('sudo ./constellation_matrix agent configure fixture'));
  await bridge.accept(approval(hire.poll, choiceId(hire, 'reject')), envelopes('late'));
  await bridge.tick();
  assert.equal(applied.length, 1);

  await bridge.accept(message('retire existing'), envelopes('decline'));
  await bridge.tick();
  const decline = find('decline');
  await bridge.accept(approval(decline.poll, choiceId(decline, 'reject')), envelopes('declined'));
  await bridge.tick();
  assert.equal(applied.length, 1);
  assert.match(sent.at(-1).content.body, /was declined/);

  time++;
  await bridge.accept(message('retire existing'), envelopes('retire'));
  await bridge.tick();
  const retire = find('retire');
  await bridge.accept(approval(retire.poll, choiceId(retire, 'approve')), envelopes('retire-accepted'));
  await bridge.tick();
  assert.match(sent.at(-1).content.body, /Retiring existing/);
  time += 30_000;
  await bridge.tick();
  assert.deepEqual(applied[1], applied[2]);
  assert.match(sent.at(-1).content.body, /Retired existing/);
  await bridge.tick();
  assert.equal(applied.length, 3);
  db.close();
});
