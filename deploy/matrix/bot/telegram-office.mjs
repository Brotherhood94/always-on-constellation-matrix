import { createHash } from 'node:crypto';
import { chmod, unlink } from 'node:fs/promises';
import { createServer } from 'node:http';
import { telegramSpaceState } from './telegram-space.mjs';

const matrixRoom = /^![^\s/\x00-\x1f]{1,254}$/u;
const eventId = /^\$[^\s\x00-\x1f]{1,254}$/u;
const username = /^@[a-z0-9_]{4,32}$/iu;
const groupId = /^(chat|channel):[1-9][0-9]{0,18}$/u;
const groupLink = /^https:\/\/t\.me\/c\/([1-9][0-9]{0,18})(?:\/[1-9][0-9]*)?\/?$/u;
const roomRoute = room => '/_matrix/client/v3/rooms/' + encodeURIComponent(room);
const matrixLink = (room, event) => 'https://matrix.to/#/' + encodeURIComponent(room) +
  (event ? '/' + encodeURIComponent(event) : '');
const bounded = (text, size) => typeof text === 'string' && text.trim() && Buffer.byteLength(text) <= size;
const fail = message => ({ ok: false, message });

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
  const direct = username.test(peer);
  if (!direct && !groupId.test(peer)) return fail('Use @username, a Telegram group message link, or a chat: or channel: peer ID.');
  if (!key) return fail('Activate Telegram Bridge before opening a chat from Office.');
  const endpoint = '/_matrix/provision/v3/' + (direct ? 'create_dm/' : 'constellation_group/') +
    encodeURIComponent(peer) + '?' + new URLSearchParams({ user_id: owner });
  const response = await bridgeFetch('http://telegram-bridge:29317' + endpoint, {
    method: 'POST', redirect: 'error', headers: { authorization: 'Bearer ' + key }, signal: AbortSignal.timeout(23_000),
  });
  if (!response.ok) return fail(direct ? 'The bridge could not open that username. Check the spelling and Telegram Bridge sign-in.'
    : 'The bridge could not open that group. Run !tg sync-chats in Telegram Bridge and check the group ID.');
  const result = await response.json();
  const room = direct ? result.dm_room_mxid : result.room;
  if (!matrixRoom.test(room ?? '') || room === space || room === control) return fail('The bridge did not return a Telegram room.');
  const verified = await database.query('SELECT room FROM telegram_space_sent_portal WHERE owner = $1 AND room = $2', [owner, room]);
  if (!verified.rows.length) return fail('The bridge did not verify the chat as an eligible Telegram room.');
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
