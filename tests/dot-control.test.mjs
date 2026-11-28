import assert from 'node:assert/strict';
import { test } from 'node:test';
import { controlRoomReady, controlTimeline } from '../agents/connector/dot-control.mjs';

const event = (type, content, state_key = '') => ({ type, content, state_key });
const members = ['@owner:fixture.test', '@dot:fixture.test', '@the-architect:fixture.test'];
const encryptedRoom = [event('m.room.create', { 'm.federate': false }),
  event('m.room.join_rules', { join_rule: 'invite' }),
  event('m.room.history_visibility', { history_visibility: 'joined' }),
  event('m.room.encryption', { algorithm: 'm.megolm.v1.aes-sha2' }),
  ...members.map(id => event('m.room.member', { membership: 'join' }, id))];

test('Dot verifies the full joined audience and refuses extra participants', () => {
  assert.equal(controlRoomReady(encryptedRoom, members), true);
  assert.equal(controlRoomReady(encryptedRoom, members.slice(0, 2)), false);
  assert.equal(controlRoomReady([...encryptedRoom,
    event('m.room.member', { membership: 'invite' }, '@other:fixture.test')], members), false);
});

test('Control Room history searches decrypted text across paginated events and counts missing keys', async () => {
  const raw = id => ({ event_id: id, sender: members[0], type: 'm.room.encrypted' });
  const client = { doRequest: async (_method, path, query) => {
    assert.equal(path, '/_matrix/client/v3/rooms/!control%3Afixture.test/messages');
    assert.equal(query.from, 'next-page');
    return { start: 'next-page', end: 'last-page', chunk: [raw('$root'), raw('$reply'), raw('$unknown')] };
  } };
  const decrypt = async item => {
    if (item.event_id === '$unknown') throw new Error('missing key');
    return { type: 'm.room.message', content: { msgtype: 'm.text',
      body: item.event_id === '$root' ? 'Check Docker' : 'Docker is healthy',
      ...(item.event_id === '$reply' && { 'm.relates_to': { rel_type: 'm.thread', event_id: '$root' } }) } };
  };
  const found = await controlTimeline(client, '!control:fixture.test', decrypt, { cursor: 'next-page', thread: '$root', query: 'healthy' });
  assert.deepEqual(found.messages.map(message => message.event), ['$reply']);
  assert.equal(found.unreadable, 1);
  assert.equal(found.next_cursor, 'last-page');
});
