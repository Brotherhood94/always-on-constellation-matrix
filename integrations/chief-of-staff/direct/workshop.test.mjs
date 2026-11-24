import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isTeamReview, workshopObservation } from './review.mjs';
import { reviewPrompt } from './evidence.mjs';

const threads = [{ task_id: 'fixture', thread_url: 'https://matrix.to/#/fixture',
  replies: [{ agent: '@coder:fixture.test', number: 1, reply_kind: 'handoff' }] }];
const roster = [{ key: 'coder', target: 'workshop-target', revision: 4, profile_active: true }];

test('review uses its Workshop roster and binds persona edits to the cited managed participant', () => {
  assert.match(reviewPrompt(threads, roster), /"participant":"coder"/);
  assert.match(reviewPrompt(threads, roster), /"profile_active":true/);
  const draft = {
    kind: 'observation', sources: [{ task_id: 'sample-1', turns: [1] }],
    facts: 'Coder handed work off.', proposal: 'State the test evidence first.',
    why: 'The handoff needs verification.', verification: 'Verify the next receipt.', agent_key: 'coder',
  };
  assert.equal(workshopObservation(JSON.stringify(draft), threads, roster).workshop_target, 'workshop-target');
  assert.throws(() => workshopObservation(JSON.stringify({ ...draft, agent_key: 'unknown' }), threads, roster),
    /managed agent/);
  assert.throws(() => workshopObservation(JSON.stringify({ ...draft, agent_key: 'reviewer' }), threads,
    [...roster, { key: 'reviewer', target: 'reviewer-target' }]), /cited evidence/);
});

test('private review supports an explicit fresh text command', () => {
  const event = { type: 'm.room.message', content: { msgtype: 'm.text', body: 'review team' } };
  assert.equal(isTeamReview(event), true);
  assert.equal(isTeamReview({ ...event, content: { ...event.content, body: 'review teammate' } }), false);
  assert.equal(isTeamReview({ ...event, content: { ...event.content, 'm.relates_to': { rel_type: 'm.replace' } } }), false);
});
