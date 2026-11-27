import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { test } from 'node:test';
import { decideDispatch, guidanceFor, routingFor } from '../agents/connector/capability-dispatch.mjs';
import { acceptedMessage, threadReply } from '../agents/connector/matrix-routing.mjs';
import { mentionMessage } from '../agents/connector/matrix-format.mjs';
import { staffingOutcome, staffingReceiptKey, verifyStaffingHandoff } from '../agents/connector/staffing-office.mjs';
import { createOfficeHiring, officeHireStatus } from '../integrations/chief-of-staff/direct/office-hiring.mjs';

const owner = '@owner:fixture.test';
const planner = '@dot:fixture.test';
const manager = { key: 'task-manager', name: 'Task Manager', role: 'coordinator', active: true, status: 'Available',
  id: '@task-manager:fixture.test', contacts: ['hiring-manager'], capabilities: [] };
const staffing = { code: 'staffing', description: 'Prepare staffing proposals', resources: ['task:*'] };
const hiring = { key: 'hiring-manager', id: '@hiring-manager:fixture.test', name: 'Hiring Manager',
  role: 'worker', active: true, status: 'Available', contacts: [], capabilities: [staffing] };
const roomId = '!office:fixture.test';
const operation = { action: 'hire', key: 'research-fixture', brief: 'Research public sources and check citations.', web: true };
const client = { getJoinedRoomMembers: async () => [owner, planner, manager.id, hiring.id] };
const catalog = { current: () => ({ agents: [manager, hiring], contactPolicy: true, dynamicHandoffs: true }),
  fresh: () => true, refresh: async () => {} };

test('Office and Dot requests become one validated, typed staffing handoff', async () => {
  for (const sender of [owner, planner]) {
    const { names, policy } = routingFor(catalog, {}, owner, planner);
    const original = { sender, room_id: roomId, type: 'm.room.message', event_id: '$request', origin_server_ts: 10,
      content: { ...mentionMessage('Chiedi a Hiring Manager di assumere research-fixture per ricerche web.', manager),
        ...(sender === planner && { 'net.matrix_agents.dot_plan': { task_id: 'fixture-task' } }) } };
    const options = { roomId, owner, planner, agents: Object.keys(names), displayNames: names, activatedAt: 0, routingPolicy: policy };
    const task = acceptedMessage(original, { ...options, userId: manager.id });
    assert.match(guidanceFor(catalog, task, manager.id, ''), /explicit request to hire/);
    const text = 'Prepare a private proposal.\nHANDOFF: ' +
      JSON.stringify({ agent: hiring.key, capability: 'staffing', resource: 'task:hiring', staffing: operation });
    const decision = await decideDispatch(catalog, task, text, manager.id, client, roomId);
    const handoff = { sender: manager.id, event_id: '$handoff', origin_server_ts: 11, room_id: roomId, type: 'm.room.message',
      content: threadReply(task, text, [hiring], null, [], {}, decision) };
    const assignment = acceptedMessage(handoff, { ...options, userId: hiring.id });
    assert.equal(assignment.delegator, manager.id);
    const events = new Map([['$request', original], ['$handoff', handoff]]);
    const verification = { read: id => events.get(id), request: assignment.request, handoff: assignment.id,
      thread: assignment.thread, manager: manager.id, hiring: hiring.id, owner, planner };
    assert.deepEqual(await verifyStaffingHandoff(verification), { operation, requester: sender });
    assert.equal(await verifyStaffingHandoff({ ...verification, thread: '$different' }), null);
    handoff.sender = hiring.id;
    assert.equal(await verifyStaffingHandoff(verification), null);
  }
});

test('restarted staffing work reports the actual approval and waits for provider and web grant', async () => {
  const db = new DatabaseSync(':memory:');
  db.exec('CREATE TABLE roster_requests (request TEXT,operation TEXT,poll TEXT,action TEXT,announced TEXT,done INTEGER)');
  const notices = [];
  let applications = 0;
  let time = 0;
  let registered;
  const options = { db, client: { getJoinedRoomMembers: async () => [registered?.id] }, office: roomId, owner, planner, enabled: true,
    catalog: { fresh: () => true, current: () => ({ agents: registered ? [registered] : [] }) },
    approvals: { enqueue: (id, profile) => db.prepare('INSERT INTO roster_requests VALUES (?, ?, NULL, NULL, NULL, 0)').run(id, JSON.stringify(profile)) },
    say: async () => { applications++; return '$private'; },
    draft: async key => ({ key }), allowedKey: () => true, now: () => time,
    publish: async (_request, status, message) => { notices.push([status, message]); return '$status'; },
  };
  let service = createOfficeHiring(options);
  const event = { id: '$handoff', thread: '$thread', request: '$request', delegator: manager.id };
  service.queue(event, { operation, requester: planner });
  await service.tick();
  await service.tick();
  time += 3000;
  await service.tick();
  service = createOfficeHiring(options);
  service.queue(event, { operation, requester: planner });
  time += 3000;
  await service.tick();
  assert.equal(applications, 1);
  assert.deepEqual(notices.map(([status]) => status), ['preparing']);
  db.exec("UPDATE roster_requests SET poll='$poll'");
  time += 3000;
  await service.tick();
  assert.deepEqual(notices.at(-1)[1]['m.mentions'], { user_ids: [planner] });
  assert.equal(notices.at(-1)[1]['m.relates_to'].event_id, '$thread');
  assert.equal(notices.at(-1)[1][staffingReceiptKey].status, 'awaiting_approval');
  assert.doesNotMatch(notices.at(-1)[1].body, /\$private|\$poll|Research public sources/);
  db.exec("UPDATE roster_requests SET action='approve',announced='provisioned',done=1");
  time += 3000;
  await service.tick();
  assert.equal(notices.at(-1)[0], 'provider_required');
  registered = { key: operation.key, id: '@research-fixture:fixture.test', active: true, status: 'Registered' };
  time += 3000;
  await service.tick();
  assert.equal(notices.at(-1)[0], 'awaiting_readiness');
  registered.status = 'Available';
  registered.grants = { other: { kind: 'github' } };
  time += 3000;
  await service.tick();
  assert.equal(notices.at(-1)[0], 'awaiting_web');
  registered.grants.web = { kind: 'mcp', purpose: 'public-web' };
  time += 3000;
  await service.tick();
  const receipt = notices.at(-1)[1][staffingReceiptKey];
  assert.equal(receipt.status, 'ready');
  assert.deepEqual(staffingOutcome(receipt, '$verified').evidence, ['$verified']);
  assert.equal(officeHireStatus({ announced: 'expired' }, registered, new Set(), false), 'expired');
  assert.equal(officeHireStatus({ announced: 'declined' }, registered, new Set(), false), 'declined');
  db.close();
});
