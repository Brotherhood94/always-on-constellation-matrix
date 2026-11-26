export async function telegramSpaceState(client, { space, owner, guide }) {
  const state = await client.getRoomState(space);
  const roomState = type => state.find(event => event.type === type && event.state_key === '')?.content;
  const creation = roomState('m.room.create');
  const active = new Map(state.filter(event => event.type === 'm.room.member' &&
    ['join', 'invite'].includes(event.content?.membership))
    .map(event => [event.state_key, event.content.membership]));
  if (creation?.type !== 'm.space' || creation['m.federate'] !== false ||
      roomState('m.room.join_rules')?.join_rule !== 'invite' || roomState('m.room.tombstone') ||
      active.size !== 2 || active.get(owner) !== 'join' || active.get(guide) !== 'join') return null;
  return state;
}

export async function reconcileTelegramRooms(client, database, { space, domain, owner, guide, control,
  key, bridgeFetch = fetch }) {
  const { rows } = await database.query('SELECT room, peer FROM telegram_space_sent_portal WHERE owner = $1 ORDER BY room', [owner]);
  const state = await telegramSpaceState(client, { space, owner, guide });
  if (!state) return;

  // An empty child event means someone intentionally removed the room from the Space.
  const children = new Map(state.filter(event => event.type === 'm.space.child')
    .map(event => [event.state_key, Boolean(event.content?.via?.length)]));
  const route = '/_matrix/client/v3/rooms/' + encodeURIComponent(space) + '/state/m.space.child/';
  const joined = new Set(await client.getJoinedRooms());
  let attempts = 0;
  for (const { room, peer } of rows) {
    if (!/^![^\s/]+$/u.test(room) || room === space || room === control) continue;
    if (!children.has(room)) {
      await client.doRequest('PUT', route + encodeURIComponent(room), null, { via: [domain], suggested: false }, 15000);
      children.set(room, true);
    }
    if (joined.has(room) && !children.get(room)) {
      await leaveTelegramPortal(client, room);
      continue;
    }
    if (!joined.has(room) && children.get(room) && key && attempts < 8) {
      if (!/^(user|chat|channel):[1-9][0-9]{0,18}$/u.test(peer)) continue;
      if ((lastInvitation.get(room) ?? 0) + 60000 > Date.now()) continue;
      attempts++;
      lastInvitation.set(room, Date.now());
      const response = await bridgeFetch(
        'http://telegram-bridge:29317/_matrix/provision/v3/constellation_membership/' + encodeURIComponent(peer) +
        '?' + new URLSearchParams({ user_id: owner, room_id: room }), { method: 'POST', redirect: 'error',
          headers: { authorization: 'Bearer ' + key }, signal: AbortSignal.timeout(5000) }).catch(() => null);
      if (!response?.ok || (await response.json()).room !== room) continue;
      try {
        await client.joinRoom(room);
      } catch (error) {
        if (!['M_FORBIDDEN', 'M_NOT_FOUND'].includes(error.body?.errcode)) throw error;
      }
    }
  }
  for (const room of lastInvitation.keys()) if (!children.get(room) || joined.has(room)) lastInvitation.delete(room);
}

const lastInvitation = new Map();

export async function leaveTelegramPortal(client, room) {
  if ((await client.getJoinedRooms()).includes(room)) await client.doRequest('POST',
    '/_matrix/client/v3/rooms/' + encodeURIComponent(room) + '/leave', null, {}, 15000);
}
