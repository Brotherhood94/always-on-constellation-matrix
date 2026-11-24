import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { gapKey } from '../../../agents/connector/agent-catalog.mjs';
import { capabilityReview, confirmedGap, recommendation } from './capability-review.mjs';

const observer = '@chief-of-staff:matrix.test';
const reporter = '@analyst:matrix.test';
const owner = '@owner:matrix.test';
const room = '!agents:matrix.test';
const available = { dynamicHandoffs: true, agents: [{ id: reporter, role: 'worker', capabilities: [] }] };
const signal = { capability: 'illustrate', resource: 'task:diagram', request: '$req1', reason: 'A diagram is required.' };
const event = { sender: reporter, content: { msgtype: 'm.text', [gapKey]: signal,
  'm.mentions': { user_ids: [observer] }, 'm.relates_to': { rel_type: 'm.thread', event_id: '$thread1',
    'm.in_reply_to': { event_id: '$request' } } } };
assert.deepEqual(confirmedGap(event, observer, available), signal);
assert.equal(confirmedGap({ ...event, sender: owner }, observer, available), null);
assert.equal(confirmedGap(event, '@other:matrix.test', available), null);
const db = new DatabaseSync(':memory:');
let review = capabilityReview(db, room, owner);
const entry = { event: '$event1', thread: '$thread1', at: 1, capability_gap: signal };
review.collect(entry);
review.collect(entry);
review.collect({ ...entry, event: '$duplicateEvidence' });
review.plan(available);
let [first] = review.pending();
assert.equal(review.pending().length, 1);
assert.match(JSON.parse(first.data).content.body, /Defer creating an agent/);
review.sent(first.id, '$report1');
review = capabilityReview(db, room, owner);
assert.equal(review.pending()[0].matrix_event, '$report1');
review.archived(first.id);
review.plan(available);
assert.equal(review.pending().length, 0);

review.collect({ ...entry, at: 2, event: '$event2', thread: '$thread2', capability_gap: { ...signal, request: '$req2' } });
review.plan(available);
const [second] = review.pending();
const { content, observation } = JSON.parse(second.data);
assert.equal(observation.proposal_type, 'new');
assert.equal(observation.sources.length, 2);
assert.match(observation.observation_id, /^cos-gap-/);
assert.deepEqual(content['m.mentions'].user_ids, [owner]);
assert.equal(content['m.relates_to'].event_id, '$report1');
assert.equal(recommendation([{ request: '$a' }], signal, { agents: [{ id: reporter, role: 'worker', name: 'Illustrator',
  capabilities: [{ code: signal.capability, resources: [signal.resource] }] }] }).kind, 'existing');
db.close();
console.log('Chief of Staff deduplication, repeated evidence, and private proposal passed.');
