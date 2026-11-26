import { createHash } from 'node:crypto';
import { chmod, unlink } from 'node:fs/promises';
import { createServer } from 'node:http';
import { leaveTelegramPortal, telegramSpaceState } from './telegram-space.mjs';

const matrixRoom = /^![^\s/\x00-\x1f]{1,254}$/u;
const eventId = /^\$[^\s\x00-\x1f]{1,254}$/u;
const username = /^@[a-z0-9_]{4,32}$/iu;
const userPeer = /^user:([1-9][0-9]{0,18})$/u;
const groupId = /^(chat|channel):[1-9][0-9]{0,18}$/u;
const groupLink = /^https:\/\/t\.me\/c\/([1-9][0-9]{0,18})(?:\/[1-9][0-9]*)?\/?$/u;
const bridgeProvisioning = 'http://telegram-bridge:29317/_matrix/provision/v3/';
const roomRoute = room => '/_matrix/client/v3/rooms/' + encodeURIComponent(room);
const matrixLink = (room, event) => 'https://matrix.to/#/' + encodeURIComponent(room) +
  (event ? '/' + encodeURIComponent(event) : '');
const bounded = (text, size) => typeof text === 'string' && text.trim() && Buffer.byteLength(text) <= size;
const fail = message => ({ ok: false, message });

function similarity(left, right) {
  let previous = Array.from({ length: right.length + 1 }, (_, i) => i);
  for (let i = 0; i < left.length; i++) {
    const next = [i + 1];
    for (let j = 0; j < right.length; j++) {
      next.push(Math.min(next[j] + 1, previous[j + 1] + 1, previous[j] + Number(left[i] !== right[j])));
    }
    previous = next;
  }
  return 1 - previous[right.length] / Math.max(left.length, right.length, 1);
}

const normalizeName = value => value?.normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase().trim().replace(/\s+/gu, ' ');

function bestLinkedMatches(rooms, query) {
  if (query.length < 3 || /^[@!]/u.test(query)) return [];
  const ranked = rooms.filter(room => normalizeName(room.name)).map(room => {
    const name = normalizeName(room.name);
    return { room, score: name.startsWith(query + ' ') ? 0.94 : similarity(name, query) };
  }).filter(entry => entry.score >= 0.8).sort((a, b) => b.score - a.score || a.room.room.localeCompare(b.room.room));
  if (!ranked.length) return [];
  if (ranked[0].score >= 0.88 && (!ranked[1] || ranked[0].score - ranked[1].score >= 0.08)) return [ranked[0].room];
  // A weak singleton needs a deliberate choice as much as tied candidates do.
  return ranked.slice(0, 10).map(entry => ({ ...entry.room, needsChoice: true }));
}

async function bridgeOpenFailure(response, direct, userId) {
  const { errcode, error } = (await response.json().catch(() => null)) ?? {};
  if (errcode === 'FI.MAU.NOT_LOGGED_IN') {
    return fail('Your Matrix account has no Telegram login. Open Telegram → Telegram Bridge and use !tg login qr to connect.');
  }
  if (errcode === 'M_FORBIDDEN' && error === 'Login is not logged in') {
    return fail('Your Telegram session is disconnected. Open Telegram → Telegram Bridge and use !tg list-logins to check it.');
  }
  if (response.status === 401 || errcode === 'M_UNKNOWN_TOKEN' || errcode === 'M_MISSING_TOKEN' ||
      (errcode === 'M_FORBIDDEN' && error === 'Provisioning API is disabled')) {
    return fail('The Office connection to Telegram Bridge needs repair. Run ./constellation_matrix telegram layout on the VPS.');
  }
  if (response.status === 429 || errcode === 'M_LIMIT_EXCEEDED') {
    return fail('Telegram Bridge is rate-limited. Wait before retrying the request.');
  }
  if (errcode === 'M_NOT_FOUND') {
    if (userId) return fail('The bridge could not resolve that Telegram user ID. Use !tg search <name> in Telegram Bridge and check the contact ID.');
    return fail(direct
      ? 'Telegram could not resolve that exact username. Check it with !tg resolve-identifier @username in Telegram → Telegram Bridge.'
      : 'Telegram Bridge could not find that group. Run !tg sync-chats in Telegram Bridge and check the group ID.');
  }
  if (response.status === 404 || response.status === 405 || errcode === 'M_UNRECOGNIZED') {
    return fail('The running bridge is missing the Office chat-opening route. Run ./constellation_matrix telegram layout on the VPS.');
  }
  const check = userId ? 'Use !tg search <name> in Telegram Bridge and check the contact ID.'
    : direct ? 'Check the username with !tg resolve-identifier @username in Telegram Bridge.'
      : 'Check the group ID in Telegram Bridge.';
  return fail(`Telegram Bridge could not open the chat (HTTP ${response.status}). ${check}`);
}

export async function privateTelegramControl(client, { control, owner, guide, domain, bridgeControl = false }) {
  const state = await client.getRoomState(control);
  const find = type => state.find(event => event.type === type && event.state_key === '')?.content;
  const members = new Map(state.filter(event => event.type === 'm.room.member' &&
    ['join', 'invite'].includes(event.content?.membership)).map(event => [event.state_key, event.content.membership]));
  const allowed = new Set([owner, guide, (bridgeControl ? '@telegrambot:' : '@dot:') + domain]);
  return find('m.room.encryption')?.algorithm === 'm.megolm.v1.aes-sha2' &&
    find('m.room.join_rules')?.join_rule === 'invite' && find('m.room.create')?.['m.federate'] === false &&
    !find('m.room.tombstone') && members.get(owner) === 'join' && members.get(guide) === 'join' &&
    [...members.keys()].every(member => allowed.has(member));
}

async function searchTelegramUsers(query, owner, key, bridgeFetch) {
  if (!key) return 'Activate Telegram Bridge and link your Telegram account before searching.';
  const response = await bridgeFetch(bridgeProvisioning + 'search_users?' + new URLSearchParams({ user_id: owner }), {
    method: 'POST', redirect: 'error', signal: AbortSignal.timeout(15_000),
    headers: { authorization: 'Bearer ' + key, 'content-type': 'application/json' },
    body: JSON.stringify({ query }),
  }).catch(() => null);
  if (!response) return 'Telegram Bridge is unavailable. Try again after it reconnects.';
  const data = await response.json().catch(() => null);
  if (data?.errcode === 'FI.MAU.NOT_LOGGED_IN' ||
      (data?.errcode === 'M_FORBIDDEN' && data.error === 'Login is not logged in')) {
    return 'Link your Telegram account in Telegram Bridge with !tg login qr, then retry the search.';
  }
  if (response.status === 429) return 'Telegram Bridge is rate-limited. Wait before trying the search again.';
  if ([401, 403, 404].includes(response.status)) {
    return 'Search is unavailable through Telegram Agent. Try Telegram Bridge or run ./constellation_matrix telegram layout on the VPS.';
  }
  if (!response.ok || !data || (data.results !== null && !Array.isArray(data.results))) {
    return 'Telegram Bridge could not complete the search. Try a current Telegram username.';
  }
  const matches = new Map();
  for (const contact of data.results ?? []) {
    const id = contact?.id;
    if (typeof id !== 'string' || !/^[1-9][0-9]{0,18}$/u.test(id) || matches.has(id)) continue;
    const name = typeof contact.name === 'string' ? contact.name.replace(/[\p{Cc}\p{Cf}]/gu, ' ').trim().slice(0, 100) : '';
    matches.set(id, { peer: 'user:' + id, name: name || 'Telegram user' });
    if (matches.size === 10) break;
  }
  return [...matches.values()];
}

// Pass only events from the Matrix client's decrypted-event handler.
export async function telegramControlSearch(room, event, options) {
  const { client, control, owner, readKey, bridgeFetch = fetch } = options;
  const { type, event_id: id, sender, content, origin_server_ts: time } = event;
  const now = Date.now();
  if (room !== control || sender !== owner || type !== 'm.room.message' || !eventId.test(id) ||
      !Number.isFinite(time) || time < now - 600_000 || time > now + 60_000 ||
      content?.msgtype !== 'm.text' || content['m.new_content'] || content['m.relates_to'] ||
      typeof content.body !== 'string') return;
  const command = /^!tg[ \t]+search(?:[ \t]+([^\r\n]*))?$/iu.exec(content.body.trim());
  if (!command || !(await privateTelegramControl(client, options))) return;
  const query = command[1]?.trim();
  const result = bounded(query, 120) && !/[\x00-\x1f\x7f]/u.test(query)
    ? await searchTelegramUsers(query, owner, await readKey(), bridgeFetch)
    : 'Use !tg search followed by a name or Telegram username, for example !tg search Sample Name.';
  const body = !Array.isArray(result) ? result : !result.length
    ? 'Telegram returned no matches. Try a current Telegram username.'
    : ['Telegram search results (up to 10)', ...result.map(({ name, peer }) => `• ${name} · ${peer.slice(5)}`), '',
      'Check the name before using its ID with !tg pm in Telegram Bridge.'].join('\n');
  if (!(await privateTelegramControl(client, options))) return;
  const payload = await client.crypto.encryptRoomEvent(control, 'm.room.message', {
    msgtype: 'm.text', body, 'm.relates_to': { 'm.in_reply_to': { event_id: id } },
  });
  const transaction = 'telegram-search-' + createHash('sha256').update(id).digest('hex');
  await client.doRequest('PUT', roomRoute(control) + '/send/m.room.encrypted/' + transaction, null, payload, 15_000);
}

export async function telegramOfficeAction(input, options) {
  const { client, database, space, control, domain, owner, key, bridgeFetch = fetch } = options;
  if (!input || typeof input.id !== 'string' || !eventId.test(input.id) ||
      !['add', 'remove', 'draft', 'resolve', 'list', 'prepare-binding', 'create-group', 'add-members', 'history'].includes(input.action) ||
      (input.action !== 'list' && (!bounded(input.target, 120) || /[\x00-\x1f]/u.test(input.target)))) {
    return fail('Use a Telegram username, group ID, room ID, or exact chat name.');
  }
  const target = input.target?.trim();
  if (input.action === 'draft') {
    if (!['direct', 'group'].includes(input.type) || !bounded(input.text, 3500)) {
      return fail('Specify direct or group, the chat name, and a message of up to 3,500 bytes.');
    }
    if (!(await privateTelegramControl(client, options))) return fail('Telegram Agent is not ready.');
    const payload = await client.crypto.encryptRoomEvent(control, 'm.room.message', {
      msgtype: 'm.text', body: `Draft requested from Office for ${target} (${input.type === 'direct' ? 'private chat' : 'group'}).\n\n${input.text}`,
      'org.matrixconstellation.telegram.request': { origin: 'office', action: 'compose',
        destination: target, chat_type: input.type, text: input.text },
    });
    const transaction = 'office-telegram-' + createHash('sha256').update(input.id).digest('hex');
    const result = await client.doRequest('PUT', roomRoute(control) + '/send/m.room.encrypted/' + transaction,
      null, payload, 15_000);
    if (!result.event_id) return fail('Matrix did not confirm the Telegram Agent request.');
    return { ok: true, message: 'Draft requested. Open the Telegram Bridge thread to review the result and approve delivery.\n' +
      matrixLink(control, result.event_id) };
  }
  if (!database || !matrixRoom.test(space ?? '')) return fail('Activate Telegram Bridge before managing Telegram rooms.');
  const state = await telegramSpaceState(client, options);
  if (!state) return fail('The private Telegram Space is not ready.');
  const route = roomRoute(space) + '/state/m.space.child/';
  const names = normalizeName;
  const linked = new Set(state.filter(event => event.type === 'm.space.child' &&
    event.content?.via?.length).map(event => event.state_key));
  const { rows } = await database.query('SELECT room, peer, name FROM telegram_space_sent_portal WHERE owner = $1', [owner]);
  const eligible = rows.filter(({ room }) => matrixRoom.test(room) && room !== control && room !== space);
  const refreshGroups = async () => Boolean(key && (await bridgeFetch(bridgeProvisioning + 'constellation_group_action/sync-groups?' +
    new URLSearchParams({ user_id: owner }), {
    method: 'POST', redirect: 'error', signal: AbortSignal.timeout(15_000),
    headers: { authorization: 'Bearer ' + key, 'content-type': 'application/json' }, body: '{}',
  }).catch(() => null))?.ok);
  const privateOperation = ['prepare-binding', 'create-group', 'add-members', 'history'].includes(input.action);
  if (privateOperation && !await privateTelegramControl(client, options)) return fail('The private Telegram Bridge is unavailable.');
  if (['create-group', 'add-members', 'history'].includes(input.action)) {
    const peer = input.peer;
    const creating = input.action === 'create-group';
    const history = input.action === 'history';
    if (!creating && (!linked.has(target) || !eligible.some(room => room.room === target && room.peer === peer))) {
      return { ...fail('The selected Telegram chat is no longer linked.'), definitive: true };
    }
    if (!history && (!Array.isArray(input.members) || input.members.length < 1 || input.members.length > 20 ||
        input.members.some(member => typeof member !== 'string' || !userPeer.test(member)))) {
      return { ...fail('Use verified Telegram user IDs for the participants.'), definitive: true };
    }
    if (!creating && !history && !groupId.test(peer)) return { ...fail('Choose a Telegram group.'), definitive: true };
    if (history && (!Number.isSafeInteger(input.since) || !Number.isSafeInteger(input.before) || input.since >= input.before)) {
      return { ...fail('Choose a valid Telegram history interval.'), definitive: true };
    }
    if (!key) return { ...fail('Connect your Telegram account in Telegram Bridge.'), definitive: true };
    const response = await bridgeFetch(bridgeProvisioning + 'constellation_group_action/' + input.action + '?' +
      new URLSearchParams({ user_id: owner }), { method: 'POST', redirect: 'error', signal: AbortSignal.timeout(25_000),
      headers: { authorization: 'Bearer ' + key, 'content-type': 'application/json' },
      body: JSON.stringify(creating ? { name: target, members: input.members } :
        { room: target, peer, ...(history ? { since: input.since, before: input.before } : { members: input.members }) }),
    }).catch(() => null);
    const result = response && await response.json().catch(() => null);
    if (!response?.ok || !result) return { ...fail('Telegram did not confirm the operation. Check the login and Telegram permissions.'),
      definitive: Boolean(response && response.status >= 400 && response.status < 500) };
    if (history) {
      if (result.room !== target || result.peer !== peer || !Array.isArray(result.messages)) return fail('Telegram history could not be verified.');
      return { ok: true, messages: result.messages.slice(0, 60), complete: result.complete === true };
    }
    if (!creating && (result.room !== target || result.peer !== peer)) return fail('Telegram did not confirm the selected group.');
    if (creating) {
      if (!matrixRoom.test(result.room ?? '') || !groupId.test(result.peer ?? '') || result.room === control || result.room === space) {
        return fail('Telegram did not confirm the created group.');
      }
      const verified = await database.query('SELECT peer FROM telegram_space_sent_portal WHERE owner = $1 AND room = $2', [owner, result.room]);
      if (!verified.rows.some(row => row.peer === result.peer)) return fail('Telegram created a group, but its Matrix portal could not be verified.');
      await client.doRequest('PUT', route + encodeURIComponent(result.room), null, { via: [domain], suggested: false }, 15_000);
    }
    const receipts = Array.isArray(result.members) ? result.members.filter(member =>
      input.members.includes(member.peer) && ['added', 'already_member', 'not_added', 'unconfirmed'].includes(member.status)) : [];
    const summary = receipts.map(({ peer, status }) => peer + ' · ' + status.replaceAll('_', ' ')).join('\n');
    return { ok: true, room: result.room, peer: result.peer, message: [
      creating ? 'Telegram group created.\n' + matrixLink(result.room) : 'Telegram invitations processed.', summary,
    ].filter(Boolean).join('\n') };
  }
  if (input.action === 'prepare-binding') {
    const peer = input.peer;
    if (!matrixRoom.test(target) || !(userPeer.test(peer) || groupId.test(peer)) ||
        !linked.has(target) || !eligible.some(row => row.room === target && row.peer === peer)) {
      return fail('Open and join the verified chat under Telegram → People before requesting a draft.');
    }
    if (!key) return fail('Connect your Telegram account in Telegram Bridge and retry.');
    const response = await bridgeFetch(bridgeProvisioning + 'constellation_membership/' + encodeURIComponent(peer) +
      '?' + new URLSearchParams({ user_id: owner, room_id: target, prepare_relay: 'true', ...(input.admitOwner === true && { admit_owner: 'true' }) }), {
      method: 'POST', redirect: 'error', headers: { authorization: 'Bearer ' + key }, signal: AbortSignal.timeout(10_000),
    }).catch(() => null);
    const relay = response?.ok && await response.json().catch(() => null);
    if (relay?.status === 'joining' && relay.room === target) return { ok: false, pending: true };
    if (relay?.room !== target || typeof relay.login !== 'string' || !/^[1-9][0-9]{0,18}$/u.test(relay.login)) {
      return fail('Telegram Bridge could not verify the chat relay. Check that you are signed in and have joined the linked chat.');
    }
    return { ok: true, room: target, peer, login: relay.login };
  }
  if (input.action === 'list') return { ok: true, rooms: eligible.filter(({ room }) => linked.has(room)).slice(0, 31)
    .map(room => ({ ...room, name: room.name?.slice(0, 120) })) };
  if (input.action === 'resolve') {
    const link = groupLink.exec(target);
    const selector = link ? 'channel:' + link[1] : target;
    const selectorName = names(selector);
    const rooms = eligible.filter(({ room, peer }) => linked.has(room) &&
      (!input.type || (input.type === 'direct') === peer.startsWith('user:')));
    const rows = rooms.filter(({ room, peer, name }) => peer === selector || room === selector || names(name) === selectorName);
    let account = username.test(selector) ? selector : null;
    if (input.linkedOnly && !account) {
      const shortName = input.allowGivenName === true && input.type !== 'group' && /^\p{L}{2,}$/u.test(selectorName);
      const matches = rows.length ? rows : input.bestMatch === true ? bestLinkedMatches(rooms, selectorName) :
        shortName ? rooms.filter(({ peer, name }) => peer.startsWith('user:') && names(name)?.startsWith(selectorName + ' ')) : [];
      const allowBareUsername = input.type === 'direct' || (input.bestMatch === true && input.type === undefined);
      if (matches.length || !allowBareUsername || input.allowGivenName !== true || !username.test('@' + selector)) {
        return { ok: true, rooms: matches.slice(0, 11) };
      }
      account = '@' + selector;
    }
    if (groupId.test(selector) || userPeer.test(selector)) {
      if (rows.length) return { ok: true, rooms: rows };
      if (input.forOpening) return { ok: true, rooms: [{ peer: selector, name: selector }] };
      return { ok: true, rooms: [] };
    }
    if (account) {
      if (!key) return fail('Activate Telegram Bridge before looking up a username.');
      const response = await bridgeFetch(bridgeProvisioning + 'resolve_identifier/' + encodeURIComponent(account) +
        '?' + new URLSearchParams({ user_id: owner }), { redirect: 'error', headers: { authorization: 'Bearer ' + key },
        signal: AbortSignal.timeout(15_000) });
      if (!response.ok) {
        if (account !== selector && response.status === 404 &&
            (await response.clone().json().catch(() => null))?.errcode === 'M_NOT_FOUND') {
          return { ok: true, rooms: [] };
        }
        return bridgeOpenFailure(response, true);
      }
      const result = await response.json();
      if (typeof result.id !== 'string' || !userPeer.test('user:' + result.id)) return fail('Telegram returned an invalid user ID.');
      const peer = 'user:' + result.id;
      const known = eligible.filter(row => row.peer === peer && linked.has(row.room));
      return { ok: true, rooms: known.length || input.linkedOnly ? known : [{ peer, name: result.name || selector }] };
    }
    const namedGroups = async () => (await database.query(
      'SELECT room, peer, name FROM telegram_space_known_group WHERE owner = $1 AND lower(name) = lower($2) ORDER BY peer LIMIT 11',
      [owner, selector])).rows;
    let groups = input.type === 'direct' ? [] : await namedGroups();
    if (input.type === 'group' && input.forOpening && !rows.length && !groups.length && await refreshGroups()) groups = await namedGroups();
    if (input.type === 'group') return { ok: true, rooms: [...rows, ...groups].filter(row => !row.peer.startsWith('user:')) };
    const contacts = await searchTelegramUsers(selector, owner, key, bridgeFetch);
    if (!Array.isArray(contacts)) return fail(contacts);
    return { ok: true, rooms: [...rows, ...groups, ...contacts] };
  }
  if (input.action === 'remove') {
    const exact = eligible.filter(({ room, peer }) => room === target || peer === target);
    const matches = [...new Set((exact.length ? exact : eligible.filter(({ name }) => names(name) === names(target)))
      .filter(({ room }) => linked.has(room) || (input.retryByRoom === true && room === target &&
        state.some(event => event.type === 'm.space.child' && event.state_key === room))).map(({ room }) => room))];
    if (matches.length !== 1) return fail(matches.length ?
      'More than one room matches. Use its Matrix room ID or Telegram peer ID.' : 'No linked Telegram room matches.');
    if (linked.has(matches[0])) await client.doRequest('PUT', route + encodeURIComponent(matches[0]), null, {}, 15_000);
    if (options.bridgeControl) await leaveTelegramPortal(client, matches[0]);
    return { ok: true, message: 'Room removed from the Telegram Space. Its history is retained. Draft delivery is unavailable while it is unlinked.' };
  }
  const group = groupLink.exec(target);
  const peer = group ? 'channel:' + group[1] : target;
  const userId = userPeer.exec(peer)?.[1];
  const direct = userId !== undefined || username.test(peer);
  if ((!direct && !groupId.test(peer)) || (userId && BigInt(userId) > 9223372036854775807n)) {
    return fail('Use @username, a user: peer ID, a Telegram group message link, or a chat: or channel: peer ID.');
  }
  if (!key) return fail('Activate Telegram Bridge before opening a chat from Office.');
  const endpoint = bridgeProvisioning + (direct ? 'create_dm/' : 'constellation_group/') +
    encodeURIComponent(userId ?? peer) + '?' + new URLSearchParams({ user_id: owner });
  const open = () => bridgeFetch(endpoint, {
    method: 'POST', redirect: 'error', headers: { authorization: 'Bearer ' + key }, signal: AbortSignal.timeout(23_000),
  });
  let response = await open();
  if (!direct && response.status === 404 && await refreshGroups()) response = await open();
  if (!response.ok) return bridgeOpenFailure(response, direct, userId);
  const result = await response.json();
  const room = direct ? result.dm_room_mxid : result.room;
  if (!matrixRoom.test(room ?? '') || room === space || room === control) return fail('The bridge did not return a Telegram room.');
  const verified = await database.query('SELECT peer FROM telegram_space_sent_portal WHERE owner = $1 AND room = $2', [owner, room]);
  if (!verified.rows.length) return fail('The bridge did not verify the chat as an eligible Telegram room.');
  if ((userId || !direct) && !verified.rows.some(row => row.peer === peer)) {
    return fail(`The room did not match the requested Telegram ${direct ? 'user' : 'group'} ID.`);
  }
  if (!linked.has(room)) await client.doRequest('PUT', route + encodeURIComponent(room),
    null, { via: [domain], suggested: false }, 15_000);
  const verifiedPeer = verified.rows.find(row => row.peer === peer)?.peer ?? (verified.rows.length === 1 ? verified.rows[0].peer : null);
  if (!verifiedPeer) return fail('Telegram confirmed more than one identity for that room.');
  return { ok: true, room, peer: verifiedPeer, message: 'Chat opened under Telegram → People.\n' + matrixLink(room) };
}

export async function listenTelegramOffice(path, options, { privateLookups = false } = {}) {
  await unlink(path).catch(error => { if (error.code !== 'ENOENT') throw error; });
  let pending = Promise.resolve();
  const server = createServer(async (req, res) => {
    res.setHeader('content-type', 'application/json');
    let result;
    try {
      if (req.method !== 'POST' || req.url !== '/') {
        res.statusCode = 404;
        result = fail('Unknown Telegram Office operation.');
      } else {
        const chunks = [];
        let size = 0;
        for await (const chunk of req) {
          size += chunk.length;
          if (size > 8192) throw new Error('Request limit exceeded.');
          chunks.push(chunk);
        }
        const input = JSON.parse(Buffer.concat(chunks).toString());
        const task = pending.then(async () => {
          if (!privateLookups && ['resolve', 'list', 'prepare-binding', 'create-group', 'add-members', 'history'].includes(input?.action)) {
            return fail('Chat lookup is available in the private Telegram Agent room.');
          }
          return telegramOfficeAction(input, await options());
        });
        pending = task.catch(() => {});
        result = await task;
      }
    } catch {
      result = fail('Telegram did not confirm the action. Check Telegram services and try again.');
    }
    res.end(JSON.stringify(result));
  });
  server.requestTimeout = 7000;
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(path, resolve);
  });
  await chmod(path, 0o600);
  return server;
}
