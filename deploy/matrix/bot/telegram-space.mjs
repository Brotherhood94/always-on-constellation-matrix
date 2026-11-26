export async function reconcileTelegramRooms(client, database, { space, domain, owner, guide, control }) {
  const { rows } = await database.query('SELECT room FROM telegram_space_sent_portal WHERE owner = $1', [owner]);
  if (!rows.length) return;

  const state = await client.getRoomState(space);
  const roomState = type => state.find(event => event.type === type && event.state_key === '')?.content;
  const creation = roomState('m.room.create');
  const active = new Map(state.filter(event => event.type === 'm.room.member' &&
    ['join', 'invite'].includes(event.content?.membership))
    .map(event => [event.state_key, event.content.membership]));
  if (creation?.type !== 'm.space' || creation['m.federate'] !== false ||
      roomState('m.room.join_rules')?.join_rule !== 'invite' || roomState('m.room.tombstone') ||
      active.size !== 2 || active.get(owner) !== 'join' || active.get(guide) !== 'join') return;

  // An empty child event means someone intentionally removed the room from the Space.
  const known = new Set(state.filter(event => event.type === 'm.space.child').map(event => event.state_key));
  const route = '/_matrix/client/v3/rooms/' + encodeURIComponent(space) + '/state/m.space.child/';
  for (const { room } of rows) {
    if (!/^![^\s/]+$/u.test(room) || room === space || room === control || known.has(room)) continue;
    await client.doRequest('PUT', route + encodeURIComponent(room), null, { via: [domain], suggested: false }, 15000);
    known.add(room);
  }
}
