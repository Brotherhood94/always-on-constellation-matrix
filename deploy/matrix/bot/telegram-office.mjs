import { createHash } from 'node:crypto';
import { chmod, unlink } from 'node:fs/promises';
import { createServer } from 'node:http';
import { telegramSpaceState } from './telegram-space.mjs';

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

export async function privateTelegramControl(client, { control, owner, guide, domain }) {
  const state = await client.getRoomState(control);
  const find = type => state.find(event => event.type === type && event.state_key === '')?.content;
  const members = new Map(state.filter(event => event.type === 'm.room.member' &&
    ['join', 'invite'].includes(event.content?.membership)).map(event => [event.state_key, event.content.membership]));
  const allowed = new Set([owner, guide, '@dot:' + domain]);
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
    return 'Search is unavailable through Telegram Control. Try Telegram Bridge or run ./constellation_matrix telegram layout on the VPS.';
  }
  if (!response.ok || !data || (data.results !== null && !Array.isArray(data.results))) {
    return 'Telegram Bridge could not complete the search. Try a current Telegram username.';
  }
  const matches = new Map();
  for (const contact of data.results ?? []) {
    const id = contact?.id;
    if (typeof id !== 'string' || !/^[1-9][0-9]{0,18}$/u.test(id) || matches.has(id)) continue;
    const name = typeof contact.name === 'string' ? contact.name.replace(/[\p{Cc}\p{Cf}]/gu, ' ').trim().slice(0, 100) : '';
    matches.set(id, `• ${name || 'Telegram user'} · ${id}`);
    if (matches.size === 10) break;
  }
  if (!matches.size) return 'Telegram returned no matches. Try a current Telegram username.';
  return ['Telegram search results (up to 10)', ...matches.values(), '',
    'Check the name before using its ID with !tg pm in Telegram Bridge.'].join('\n');
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
  const body = bounded(query, 120) && !/[\x00-\x1f\x7f]/u.test(query)
    ? await searchTelegramUsers(query, owner, await readKey(), bridgeFetch)
    : 'Use !tg search followed by a name or Telegram username, for example !tg search Sample Name.';
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
      !['add', 'remove', 'draft'].includes(input.action) || !bounded(input.target, 120) ||
      /[\x00-\x1f]/u.test(input.target)) return fail('Use a Telegram username, group ID, room ID, or exact chat name.');
  const target = input.target.trim();
  if (input.action === 'draft') {
    if (!['direct', 'group'].includes(input.type) || !bounded(input.text, 3500)) {
      return fail('Specify direct or group, the chat name, and a message of up to 3,500 bytes.');
    }
    if (!(await privateTelegramControl(client, options))) return fail('Telegram Control is not ready.');
    const payload = await client.crypto.encryptRoomEvent(control, 'm.room.message', {
      msgtype: 'm.text', body: `Draft requested from Office for ${target} (${input.type === 'direct' ? 'private chat' : 'group'}).\n\n${input.text}`,
      'org.matrixconstellation.telegram.request': { origin: 'office', action: 'compose',
        destination: target, chat_type: input.type, text: input.text },
    });
    const transaction = 'office-telegram-' + createHash('sha256').update(input.id).digest('hex');
    const result = await client.doRequest('PUT', roomRoute(control) + '/send/m.room.encrypted/' + transaction,
      null, payload, 15_000);
    if (!result.event_id) return fail('Matrix did not confirm the Telegram Control request.');
    return { ok: true, message: 'Draft requested. Open the Telegram Control thread to review the result and approve delivery.\n' +
      matrixLink(control, result.event_id) };
  }
  if (!database || !matrixRoom.test(space ?? '')) return fail('Activate Telegram Bridge before managing Telegram rooms.');
  const state = await telegramSpaceState(client, options);
  if (!state) return fail('The private Telegram Space is not ready.');
  const route = roomRoute(space) + '/state/m.space.child/';
  const names = value => value?.normalize('NFKC').toLowerCase();
  const linked = new Set(state.filter(event => event.type === 'm.space.child' &&
    event.content?.via?.length).map(event => event.state_key));
  const { rows } = await database.query('SELECT room, peer, name FROM telegram_space_sent_portal WHERE owner = $1', [owner]);
  const eligible = rows.filter(({ room }) => matrixRoom.test(room) && room !== control && room !== space);
  if (input.action === 'remove') {
    const exact = eligible.filter(({ room, peer }) => room === target || peer === target);
    const matches = [...new Set((exact.length ? exact : eligible.filter(({ name }) => names(name) === names(target)))
      .filter(({ room }) => linked.has(room)).map(({ room }) => room))];
    if (matches.length !== 1) return fail(matches.length ?
      'More than one room matches. Use its Matrix room ID or Telegram peer ID.' : 'No linked Telegram room matches.');
    await client.doRequest('PUT', route + encodeURIComponent(matches[0]), null, {}, 15_000);
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
  const response = await bridgeFetch(endpoint, {
    method: 'POST', redirect: 'error', headers: { authorization: 'Bearer ' + key }, signal: AbortSignal.timeout(23_000),
  });
  if (!response.ok) return bridgeOpenFailure(response, direct, userId);
  const result = await response.json();
  const room = direct ? result.dm_room_mxid : result.room;
  if (!matrixRoom.test(room ?? '') || room === space || room === control) return fail('The bridge did not return a Telegram room.');
  const verified = await database.query('SELECT peer FROM telegram_space_sent_portal WHERE owner = $1 AND room = $2', [owner, room]);
  if (!verified.rows.length) return fail('The bridge did not verify the chat as an eligible Telegram room.');
  if (userId && !verified.rows.some(row => row.peer === peer)) return fail('The room did not match the requested Telegram user ID.');
  if (!linked.has(room)) await client.doRequest('PUT', route + encodeURIComponent(room),
    null, { via: [domain], suggested: false }, 15_000);
  return { ok: true, message: 'Room opened under Telegram → Rooms. Join if Element prompts you.\n' + matrixLink(room) };
}

export async function listenTelegramOffice(path, options) {
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
        const task = pending.then(async () => telegramOfficeAction(input, await options()));
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
