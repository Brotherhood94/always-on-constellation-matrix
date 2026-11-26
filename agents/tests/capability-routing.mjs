import { test } from "node:test";
import assert from 'node:assert/strict';
import { canDispatch, evaluateDelegation, gapKey, parseCatalog } from '../connector/agent-catalog.mjs';
import { controlsFromReply, gapNotice } from '../connector/capability-dispatch.mjs';
import { managedContext } from '../connector/managed-task.mjs';
import { acceptedMessage, mentionMessage, taskOutcomeKey, threadRelation, threadReply, turnKey } from '../connector/matrix-routing.mjs';
import { Store } from '../connector/store.mjs';

const server = 'matrix.test';
const id = name => `@${name}:${server}`;
const skill = (code, resources) => ({ code, description: code, resources });
const agent = (key, capabilities, role = 'worker', status = 'Available') => ({
  key, id: id(key), name: key, role, status, capabilities, limits: 'Test scope',
});
const profiles = [agent('manager', [skill('coordination', ['task:*'])], 'coordinator'),
  agent('analyst', [skill('analysis', ['task:*'])]), agent('coder', [skill('code', ['repo:*'])]),
  agent('new-worker', [skill('special-review', ['repo:team/project'])]),
  agent('external', [skill('inspect', ['host:external'])], 'worker', 'Unavailable'),
  agent('chief-of-staff', [], 'observer')];
const snapshot = parseCatalog(JSON.stringify({ version: 1, time: 1, agents: profiles, dynamicHandoffs: true }), server);
const live = { current: () => snapshot, fresh: () => true };
const stale = { ...live, fresh: () => false };
const names = Object.fromEntries(profiles.filter(p => p.role !== 'observer').map(p => [p.id, p.name]));
const joined = new Set(Object.keys(names));
const owner = id('owner');
const planner = id('dot');
const roomId = '!agents:matrix.test';
const manager = id('manager');
const policy = { dynamic: true, coordinator: manager, activators: [owner, planner] };
const source = (key, sender, content) => ({ event_id: key, room_id: roomId, sender, origin_server_ts: 10, type: 'm.room.message', content });
const accepted = (event, userId) => acceptedMessage(event, { roomId, owner, planner, userId, agents: Object.keys(names),
  displayNames: names, activatedAt: 0, routingPolicy: policy });
const choose = (from, to, capability, resource) => evaluateDelegation(live, id(from), joined, {
  handoff: { agent: to, capability, resource },
});
const reply = (from, task, body, decision) => threadReply(task, body, profiles.filter(p => p.id !== id(from) && p.role !== 'observer'),
  'Owner', [{ id: owner, name: 'Owner' }], {}, decision);
const events = new Map();
let incoming = source('$request', owner, mentionMessage('Coordinate a code change.', profiles[0]));
events.set(incoming.event_id, incoming);
const sequence = [['manager', 'analyst', 'analysis', 'task:spec'], ['analyst', 'coder', 'code', 'repo:team/project'],
  ['coder', 'new-worker', 'special-review', 'repo:team/project'], ['new-worker', 'coder', 'code', 'repo:team/project'],
  ...Array.from({ length: 9 }, (_, index) => index % 2 === 0
    ? ['coder', 'new-worker', 'special-review', 'repo:team/project']
    : ['new-worker', 'coder', 'code', 'repo:team/project']),
  ['new-worker', 'manager', 'coordination', 'task:spec']];
let fifth;
for (const [index, [from, to, capability, resource]] of sequence.entries()) {
  const task = accepted(incoming, id(from));
  assert.equal(task.number, index + 1);
  assert.ok(await managedContext(task, names, async key => events.get(key)));
  const decision = index === 13 ? choose(from, 'coder', 'code', 'repo:team/project') : choose(from, to, capability, resource);
  const outgoing = reply(from, task, 'Work and evidence.', decision);
  assert.deepEqual(outgoing['m.mentions'].user_ids, [id(to)]);
  assert.equal(outgoing[taskOutcomeKey], undefined);
  assert.equal(outgoing[turnKey].managed.version, 2);
  incoming = source(`$reply${index + 1}`, id(from), outgoing);
  events.set(incoming.event_id, incoming);
  fifth = task;
}
const closure = accepted(incoming, manager);
assert.equal(closure.number, 15);
assert.ok(await managedContext(closure, names, async key => events.get(key)));
const final = reply('manager', closure, 'Verified.\nTASK_OUTCOME: {"status":"completed","result":"done","verification":"checked"}', {});
assert.equal(final[taskOutcomeKey].status, 'completed');
assert.deepEqual(final['m.mentions'].user_ids, [owner]);
const overLimit = { ...incoming.content, [turnKey]: { ...incoming.content[turnKey], number: 15 } };
assert.equal(accepted(source('$reply15', id('new-worker'), overLimit), manager), null);
const failedAssignment = accepted(events.get('$reply2'), id('coder'));
const failure = reply('coder', failedAssignment, 'TASK_OUTCOME: ' + JSON.stringify({
  status: 'blocked',
  blocker: 'Codex ACP failed while loading the saved session.',
  unblocker: 'Manager',
  action: 'Investigate the saved session and preserve the published artifact.',
}), choose('coder', 'new-worker', 'special-review', 'repo:team/project'));
assert.deepEqual(failure['m.mentions'].user_ids, [manager]);
assert.equal(failure[taskOutcomeKey], undefined);
assert.equal(failure[turnKey].managed.target, manager);
assert.match(failure.body, /loading the saved session/);
assert.match(failure.body, /Investigate the saved session and preserve the published artifact/);
assert.doesNotMatch(failure.body, /mention .* again/i);
const forged = structuredClone(incoming);
forged.content['m.relates_to']['m.in_reply_to'].event_id = '$reply3';
assert.equal(await managedContext(accepted(forged, manager), names, id => Promise.resolve(id === forged.event_id ? forged : events.get(id))), null);
assert.equal(choose('coder', 'new-worker', 'special-review', 'repo:team/other').target, undefined);
assert.equal(canDispatch(profiles[3], new Set()), false);

const gap = (capability, resource) => ({ gap: { capability, resource, reason: 'Required for the work.' } });
assert.equal(evaluateDelegation(live, id('coder'), joined, gap('inspect', 'host:external')).gap, undefined);
assert.equal(evaluateDelegation(stale, id('coder'), joined, gap('missing', 'host:external')).gap, undefined);
const missing = evaluateDelegation(live, id('coder'), joined, gap('missing', 'host:external'));
assert.equal(missing.gap.capability, 'missing');
const notice = gapNotice(fifth, missing, threadRelation(fifth));
assert.deepEqual(notice['m.mentions'].user_ids, [id('chief-of-staff')]);
assert.equal(notice[turnKey], undefined);
assert.equal(notice[gapKey].request, '$request');
assert.equal(accepted(source('$gap', id('coder'), notice), id('analyst')), null);
assert.deepEqual(controlsFromReply('Text.\nHANDOFF: {"agent":"new-worker","capability":"special-review","resource":"repo:team/project"}\nATTACH_FILES: ["x"]').handoff.agent, 'new-worker');

const store = new Store(':memory:');
store.enqueue(fifth);
store.saveOutgoing(fifth.id, { notice, content: final });
assert.deepEqual(store.next().outgoing.notice, notice);
store.sent(fifth.id, '$sent');
assert.equal(store.next(), undefined);
store.db.close();
console.log('Catalog targeting, 15-reply provenance, and durable capability notice passed.');

test('directed contacts limit dispatch without turning a routing restriction into a hiring gap', () => {
  const a = {key:'a', id:'@a:x', role:'worker', status:'Available', contacts:[], capabilities:[]};
  const b = {key:'b', id:'@b:x', role:'worker', status:'Available', contacts:['a'],
    capabilities:[{code:'review',resources:['task:*']}]};
  const value = {contactPolicy:true, agents:[a,b]};
  const fixture = {fresh:()=>true, current:()=>value};
  const wanted = {agent:'b',capability:'review',resource:'task:example'};
  assert.match(evaluateDelegation(fixture,a.id,new Set([a.id,b.id]),{handoff:wanted}).blocker, /contact/i);
  assert.equal(evaluateDelegation(fixture,a.id,new Set([a.id,b.id]),{gap:{...wanted,reason:'check'}}).gap, undefined);
  a.contacts.push('b');
  assert.equal(evaluateDelegation(fixture,a.id,new Set([b.id]),{handoff:wanted}).target.id, b.id);
});
