import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { acceptedMessage, mentionMessage, taskOutcomeKey, team, threadReply, turnKey } from '../connector/matrix-routing.mjs';
import { managedContext } from '../connector/managed-task.mjs';
import { Store } from '../connector/store.mjs';

const server = 'matrix.test';
const [owner, planner, manager, analyst, coder, reviewer] = ['owner', 'dot', 'manager', 'analyst', 'coder', 'reviewer']
  .map(name => `@${name}:${server}`);
const displayNames = team(server);
const agents = Object.keys(displayNames);
const roomId = '!agents:matrix.test';
const recipients = [{ id: planner, name: 'Dot' }, { id: owner, name: 'Owner' }];
const event = (event_id, sender, content) => ({ event_id, room_id: roomId, sender, type: 'm.room.message', origin_server_ts: 10, content });
const accept = (incoming, userId) => acceptedMessage(incoming, { roomId, owner, planner, userId, agents, displayNames, activatedAt: 0 });
const send = (task, message, sender) => threadReply(task, message, agents.filter(id => id !== sender)
  .map(id => ({ id, name: displayNames[id] })), 'Owner', recipients);
const received = new Map();
const load = async id => received.get(id);
const first = event('$requested', owner, mentionMessage('Implement with independent review.', { id: manager, name: 'Manager' }));
first.content['m.relates_to'] = { rel_type: 'm.thread', event_id: '$thread' };
received.set(first.event_id, first);
const initial = accept(first, manager);
assert.deepEqual(initial?.managed, { manager });
assert.equal(initial.request, '$requested');
assert.equal(initial.thread, '$thread');

const dispatch = send(initial, 'Plan.\nHANDOFF: Analyst\nHANDOFF_ROUTE: ["Analyst","Coder","Reviewer","Coder"]', manager);
const chain = [manager, analyst, coder, reviewer, coder, manager];
let content = dispatch;
for (let index = 0; index < chain.length - 1; index += 1) {
  const [sender, recipient] = chain.slice(index, index + 2);
  assert.deepEqual(content['m.mentions'].user_ids, [recipient]);
  assert.equal(content[taskOutcomeKey], undefined);
  assert.equal(content[turnKey].number, index + 1);
  const incoming = event(`$handoff${index}`, sender, content);
  const task = accept(incoming, recipient);
  assert.ok(task);
  assert.ok(await managedContext(task, displayNames, load));
  received.set(incoming.event_id, incoming);
  if (recipient === manager) {
    content = send(task, 'TASK_OUTCOME: {"status":"completed","result":"Report reviewed","verification":"Reviewer confirmed the result"}', manager);
  } else {
    // An attempted worker-level outcome or alternate handoff never overrides the Manager's path.
    content = send(task, 'Evidence.\nTASK_OUTCOME: {"status":"completed","result":"Only my part","verification":"Local check"}\nHANDOFF: Manager', recipient);
  }
}
assert.equal(content[turnKey].number, 6);
assert.equal(content[taskOutcomeKey].status, 'completed');
assert.deepEqual(content['m.mentions'].user_ids, [planner, owner]);
assert.equal(content['m.relates_to'].event_id, '$thread');

const delegated = accept(received.get('$handoff0'), analyst);
const workerBlocked = send(delegated, 'TASK_OUTCOME: {"status":"blocked","blocker":"Missing input","unblocker":"Owner","action":"Provide data"}', analyst);
assert.equal(workerBlocked[taskOutcomeKey], undefined);
assert.deepEqual(workerBlocked['m.mentions'].user_ids, [manager]);
assert.match(workerBlocked.body, /Missing input/);
const returning = accept(event('$workerBlocked', analyst, workerBlocked), manager);
assert.ok(await managedContext(returning, displayNames, load));
const terminalBlock = send({ ...returning, number: 15 }, 'More work.\nHANDOFF: Coder', manager);
assert.equal(terminalBlock[taskOutcomeKey].status, 'blocked');
assert.deepEqual(terminalBlock['m.mentions'].user_ids, [planner, owner]);

const forged = structuredClone(received.get('$handoff1'));
forged.content[turnKey].managed.dispatch = '$unknown';
assert.equal(await managedContext(accept(forged, coder), displayNames, load), null);
const absentRoute = structuredClone(received.get('$handoff0'));
delete absentRoute.content[turnKey].managed;
assert.equal(accept(absentRoute, analyst), null);
const routeTooLong = send({ ...initial, number: 13 }, 'Plan.\nHANDOFF: Analyst\nHANDOFF_ROUTE: ["Analyst","Coder"]', manager);
assert.equal(routeTooLong[taskOutcomeKey].status, 'blocked');

const question = event('$question', owner, mentionMessage('What is the current status? Reply to me directly.',
  { id: manager, name: 'Manager' }));
const direct = accept(question, manager);
assert.equal(direct?.replyTo, owner);
const answer = send(direct, 'Current result.\nHANDOFF: Coder', manager);
assert.deepEqual(answer['m.mentions'].user_ids, [owner]);
assert.equal(answer[taskOutcomeKey], undefined);
assert.equal(answer[turnKey].managed, undefined);

const directory = mkdtempSync(join(tmpdir(), 'manager-store-'));
try {
  const database = join(directory, 'connector.sqlite3');
  let store = new Store(database);
  store.enqueue(returning);
  store.db.close();
  store = new Store(database);
  try {
    assert.deepEqual(store.next().managed, returning.managed);
    assert.equal(store.next().request, initial.request);
    store.enqueue({ ...returning, id: '$duplicateWorkerReply' });
    store.sent(returning.id, '$terminal', '❌');
    assert.equal(store.next(), undefined);
    assert.equal(store.nextReactionCleanup().request, initial.request);
  } finally {
    store.db.close();
  }
} finally {
  rmSync(directory, { recursive: true, force: true });
}
console.log('Manager routing, provenance, outcomes, direct replies and 15-reply limit verified.');
