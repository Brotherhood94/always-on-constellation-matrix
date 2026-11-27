import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { acceptsContact, evaluateDelegation } from '../agents/connector/agent-catalog.mjs';
import { helperQuestion, privateHelperRoom, supportEvidence } from '../agents/connector/helper-support.mjs';
import { assignedHandoff } from '../agents/connector/managed-task.mjs';
import { turnKey } from '../agents/connector/matrix-routing.mjs';

const owner = '@operator:fixture.test';
const helper = '@helper:fixture.test';
const taskManager = '@task-manager:fixture.test';
const coach = '@coach:fixture.test';
const office = '!office:fixture.test';
const dm = '!helper:fixture.test';
const handoff = { capability: 'constellation-support', resource: 'workspace:constellation-matrix' };
const roster = { agents: [
  { id: taskManager, key: 'task-manager', role: 'coordinator', active: true, status: 'Available', contacts: ['helper'], capabilities: [] },
  { id: coach, key: 'coach', role: 'worker', active: true, status: 'Available', contacts: ['helper'], capabilities: [] },
  { id: helper, key: 'helper', role: 'worker', active: true, status: 'Available', contacts: [coach], capabilities: [
    { code: handoff.capability, resources: [handoff.resource] },
  ] },
] };

test('Helper requires a verified Task Manager handoff in both routing modes', async () => {
  const start = { sender: owner, content: { 'm.mentions': { user_ids: [taskManager] } } };
  const dispatch = { sender: taskManager, content: {
    [turnKey]: { request: '$original', number: 1, handoff },
    'm.relates_to': { rel_type: 'm.thread', event_id: '$original', 'm.in_reply_to': { event_id: '$original' } },
    'm.mentions': { user_ids: [helper] },
  } };
  for (const contactPolicy of [false, true]) {
    const snapshot = { ...roster, contactPolicy };
    const catalog = { current: () => snapshot, fresh: () => true };
    const joined = new Set(snapshot.agents.map(agent => agent.id));
    const controls = { handoff: { ...handoff, agent: 'helper' } };
    const policy = { snapshot, fresh: true };
    assert.equal(evaluateDelegation(catalog, taskManager, joined, controls).target.id, helper);
    assert.ok(evaluateDelegation(catalog, coach, joined, controls).blocker);
    assert.equal(acceptsContact(policy, dispatch, helper), true);
    assert.equal(acceptsContact(policy, { ...dispatch, sender: coach }, helper), false);
    assert.equal(acceptsContact({ ...policy, fresh: false }, dispatch, helper), false);
    const names = Object.fromEntries(snapshot.agents.map(agent => [agent.id, agent.key]));
    const read = async id => id === '$original' ? start : null;
    assert.equal(await assignedHandoff(dispatch, '$original', '$original', helper, names, [owner], read), true);
    assert.equal(await assignedHandoff(dispatch, '$fake', '$original', helper, names, [owner], read), false);
  }
});

test('Helper accepts plain text from the operator only in its private encrypted room', () => {
  const state = [
    { type: 'm.room.create', state_key: '', content: { 'm.federate': false } },
    { type: 'm.room.join_rules', state_key: '', content: { join_rule: 'invite' } },
    { type: 'm.room.encryption', state_key: '', content: { algorithm: 'm.megolm.v1.aes-sha2' } },
    ...[owner, helper].map(id => ({ type: 'm.room.member', state_key: id, content: { membership: 'join' } })),
  ];
  assert.equal(privateHelperRoom(state, owner, helper), true);
  assert.equal(privateHelperRoom([...state, { type: 'm.room.member', state_key: taskManager, content: { membership: 'invite' } }],
    owner, helper), false);
  const question = { room_id: dm, sender: owner, type: 'm.room.message', event_id: '$setup', origin_server_ts: 20,
    content: { msgtype: 'm.text', body: 'How do I set up Element?' } };
  assert.deepEqual(helperQuestion(question, dm, owner, 10),
    { id: '$setup', thread: '$setup', body: question.content.body, replyTo: owner });
  assert.equal(helperQuestion({ ...question, sender: taskManager }, dm, owner, 10), null);
  assert.equal(helperQuestion({ ...question, room_id: office }, dm, owner, 10), null);
});

test('support evidence cites the deployed guide and reports stale and missing health separately', async () => {
  const folder = await mkdtemp(join(tmpdir(), 'constellation-helper-'));
  try {
    const docs = join(folder, 'guides.json');
    await writeFile(docs, JSON.stringify({ revision: 'fixture-revision', documents: [
      { path: 'docs/setup-guide.md', link: 'https://example.test/fixture/setup-guide.md',
        body: '# Set up Element\nConnect Element Desktop to the custom Matrix homeserver.' },
      { path: 'docs/team.md', body: '# Team\nCoordinate Office handoffs.' },
      { path: 'docs/dot.md', body: '# Connect Dot\nCreate a Secure MCP Tunnel and add the Constellation Office plugin.' },
    ] }));
    await writeFile(join(folder, 'integrations.json'), JSON.stringify({ time: 1, services: {
      'Context Intelligence': 'healthy', 'Private token': 'secret-token',
    } }));
    const result = JSON.parse(await supportEvidence('How do I set up Element?', { docs, dashboard: folder, now: 200 }));
    assert.equal(result.documentation, 'fixture-revision');
    assert.match(result.sources[0].link, /setup-guide/);
    assert.match(result.health.services.age, /^Stale/);
    assert.deepEqual(result.health.services.states, [['Context Intelligence', 'healthy']]);
    assert.match(result.health.agents.age, /^Missing/);
    const dot = JSON.parse(await supportEvidence('Come posso impostare Dot?', { docs, dashboard: folder, now: 200 }));
    assert.equal(dot.sources[0].link, 'docs/dot.md');
  } finally {
    await rm(folder, { recursive: true, force: true });
  }
});
