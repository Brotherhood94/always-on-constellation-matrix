import assert from 'node:assert/strict';
import { test } from 'node:test';
import { interaction, reviewPrompt, reviewThreads, writeInteraction } from './evidence.mjs';

test('a plain request carries its Matrix mentions into review independently of handoffs', () => {
  const room = '!test:example.org';
  const owner = '@owner:example.org';
  const analyst = '@analyst:example.org';
  const reviewer = '@reviewer:example.org';
  const observer = '@chief-of-staff:example.org';
  const root = '$initial';
  const stamp = 1795422681225;
  const entries = [
    interaction(room, {
      type: 'm.room.message', sender: owner, event_id: root, origin_server_ts: stamp,
      content: { msgtype: 'm.text', 'm.mentions': { user_ids: [analyst] } },
    }, observer),
    interaction(room, {
      type: 'm.room.message', sender: analyst, event_id: '$handoff', origin_server_ts: stamp + 1000,
      content: {
        msgtype: 'm.text',
        'm.mentions': { user_ids: [reviewer] },
        'm.relates_to': { rel_type: 'm.thread', event_id: root },
        'net.matrix_agents.turn': { number: 1, request: root },
      },
    }, observer),
  ];
  const prompt = reviewPrompt(reviewThreads(entries, room));
  const [{ turns: [initial, handoff] }] = JSON.parse(prompt.split('\n').at(-1));
  assert.deepEqual(initial.mentioned_recipients, [handoff.participant]);
  assert.equal(initial.handoff_to, undefined);
  assert.equal(handoff.handoff_to, 'participant-3');
  assert.equal(handoff.mentioned_recipients, undefined);
  assert.doesNotMatch(prompt, /example\.org|\$initial/);
});

test('a new assignment in the same Matrix thread creates the next task', async t => {
  const room = '!test:example.org';
  const owner = '@owner:example.org';
  const observer = '@chief-of-staff:example.org';
  const worker = '@analyst:example.org';
  const thread = '$first';
  const requests = [];
  t.mock.method(globalThis, 'fetch', async (_url, options) => {
    requests.push(JSON.parse(options.body).data.execution);
    return { ok: true, json: async () => ({ status: 'queued' }) };
  });
  const event = (id, sender, relation, turn) => ({
    type: 'm.room.message', sender, event_id: id, origin_server_ts: 1795422681225,
    content: { msgtype: 'm.text', 'm.mentions': { user_ids: [worker] },
      ...(relation && { 'm.relates_to': { rel_type: 'm.thread', event_id: thread } }),
      ...(turn && { 'net.matrix_agents.turn': turn }) },
  });
  for (const message of [event(thread, owner), event('$handoff', worker, true, { number: 1, request: thread }),
    event('$followup', owner, true), event('$reply', worker, true, { number: 1, request: '$followup' })]) {
    await writeInteraction('fixture', interaction(room, message, observer, owner));
  }
  assert.equal(new Set(requests.map(scope => scope.trace_id)).size, 1);
  assert.equal(requests[0].task_id, requests[1].task_id);
  assert.equal(requests[2].task_id, requests[3].task_id);
  assert.notEqual(requests[0].task_id, requests[2].task_id);
  assert.equal(requests[3].sequence, 1);
});
