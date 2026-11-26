import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import crypto from '@matrix-org/matrix-sdk-crypto-nodejs';
import matrix from '@vector-im/matrix-bot-sdk';
import pg from 'pg';
import { digest } from './policy.mjs';

const { MatrixClient, RustSdkCryptoStorageProvider, SimpleFsStorageProvider, LogService, EncryptedRoomEvent } = matrix;
const silent = () => {};
LogService.setLogger({ trace: silent, debug: silent, info: silent, warn: silent, error: silent });
const encryptedType = 'm.room.encrypted';
const messageType = 'm.room.message';
const bridgeStatus = 'com.beeper.message_send_status';
const route = (room) => `/_matrix/client/v3/rooms/${encodeURIComponent(room)}`;

export async function matrixConnection(config, path, { crypto: enableCrypto = true } = {}) {
  const token = (await readFile(join(path, 'matrix-token'), 'utf8')).trim();
  const storage = new SimpleFsStorageProvider(join(path, 'sdk-data.json'));
  const sdk = new MatrixClient(config.matrixUrl, token, storage,
    enableCrypto ? new RustSdkCryptoStorageProvider(join(path, 'crypto'), crypto.StoreType.Sqlite) : null);
  const who = await sdk.getWhoAmI();
  if (who.user_id !== config.agent || who.device_id !== config.device) throw new Error('Matrix identity or device changed.');
  if (enableCrypto) await sdk.crypto.prepare();

  async function request(method, endpoint, body, timeout = 15_000) {
    const response = await fetch(config.matrixUrl + endpoint, {
      method, headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' },
      body, redirect: 'error', signal: AbortSignal.timeout(timeout),
    });
    if (!response.ok) {
      const error = new Error(`Matrix HTTP ${response.status}`);
      error.status = response.status;
      const payload = await response.json().catch(() => null);
      error.code = payload?.errcode;
      throw error;
    }
    return response.json();
  }

  async function liveState(room) {
    const state = await request('GET', `${route(room)}/state`);
    const content = (type) => state.find(event => event.type === type && event.state_key === '')?.content;
    const active = state.filter(event => event.type === 'm.room.member' && ['join', 'invite'].includes(event.content?.membership));
    const members = active
      .map(event => [event.state_key, event.content.membership]).sort((a, b) => a[0].localeCompare(b[0]));
    return {
      members, audience: digest(members), name: content('m.room.name')?.name,
      names: new Map(active.map(event => [event.state_key, event.content.displayname])),
      encrypted: content('m.room.encryption')?.algorithm === 'm.megolm.v1.aes-sha2',
      private: content('m.room.join_rules')?.join_rule === 'invite' && content('m.room.create')?.['m.federate'] === false,
      tombstone: Boolean(content('m.room.tombstone')),
    };
  }

  async function verifyControl({ requireDot = false } = {}) {
    const state = await liveState(config.controlRoom);
    const members = new Map(state.members);
    if (config.bridgeControl) {
      return state.encrypted && state.private && !state.tombstone && members.size === 3 &&
        [config.owner, config.agent, config.bridgeBot].every(member => members.get(member) === 'join');
    }
    return state.encrypted && state.private && !state.tombstone &&
      members.get(config.owner) === 'join' && members.get(config.agent) === 'join' &&
      [...members.keys()].every(member => [config.owner, config.agent, config.dot].includes(member)) &&
      (!requireDot || members.get(config.dot) === 'join');
  }

  async function roomRemovedFromSpace(space, room) {
    const child = await request('GET', `${route(space)}/state/m.space.child/${encodeURIComponent(room)}`).catch(error => {
      if (error.status !== 404 || error.code !== 'M_NOT_FOUND') throw error;
    });
    return config.bridgeControl ? !child?.via?.length : child !== undefined && !child.via?.length;
  }

  async function officeEvent(room, event) {
    const raw = await request('GET', `${route(room)}/event/${encodeURIComponent(event)}`);
    if (raw.type !== encryptedType || raw.event_id !== event || raw.unsigned?.redacted_because) return null;
    const decrypted = (await sdk.crypto.decryptRoomEvent(new EncryptedRoomEvent(raw), room)).raw;
    return { ...decrypted, event_id: raw.event_id, sender: raw.sender, origin_server_ts: raw.origin_server_ts };
  }

  async function recentMessages(room, range) {
    const events = [];
    let from;
    let unreadable = 0;
    let complete = false;
    for (let page = 0; page < 4 && !complete; page++) {
      const params = new URLSearchParams({ dir: 'b', limit: '40', filter: JSON.stringify({ types: [encryptedType] }) });
      if (from) params.set('from', from);
      const batch = await request('GET', `${route(room)}/messages?${params}`);
      for (const raw of batch.chunk ?? []) {
        if (raw.origin_server_ts < range.since) {
          complete = true;
          break;
        }
        if (raw.type !== encryptedType || raw.unsigned?.redacted_because ||
            !Number.isSafeInteger(raw.origin_server_ts) || !range.includes(raw.origin_server_ts)) continue;
        let event;
        try {
          event = (await sdk.crypto.decryptRoomEvent(new EncryptedRoomEvent(raw), room)).raw;
        } catch {
          unreadable++;
          continue;
        }
        const body = event.content?.body;
        if (event.type !== messageType || event.content?.msgtype !== 'm.text' ||
            event.content['m.new_content'] || typeof body !== 'string' || !body.trim()) continue;
        events.push({ event_id: raw.event_id, sender: raw.sender, ts: raw.origin_server_ts, body });
        if (events.length === 30) return { events, unreadable, complete: false };
      }
      if (!batch.end || batch.end === from) complete = true;
      from = batch.end;
    }
    return { events, unreadable, complete };
  }

  async function sync(since, timeout = 1_000) {
    const filter = { room: { timeline: { limit: 100 } }, presence: { types: [] } };
    const params = new URLSearchParams({ filter: JSON.stringify(filter), timeout: String(timeout), set_presence: 'offline' });
    if (since) params.set('since', since);
    return request('GET', `/_matrix/client/v3/sync?${params}`, undefined, 12_000);
  }

  async function applySync(batch, dispatch) {
    const originals = new Map();
    await sdk.processSync(batch, async (name, room, event) => {
      if (name === 'room.join') await sdk.crypto.onRoomJoin(room);
      if (name === 'room.encrypted_event') originals.set(event.event_id, event);
      if (name === 'room.failed_decryption') await dispatch(room, event, true, true);
      if (name === 'room.event') {
        await sdk.crypto.onRoomEvent(room, event);
        if (!event?.event_id) return;
        const original = originals.get(event.event_id);
        if (original && event.type === encryptedType) return;
        await dispatch(room, original ? { ...event, sender: original.sender, unsigned: original.unsigned,
          origin_server_ts: original.origin_server_ts } : event, Boolean(original), false);
      }
    });
  }

  async function encryptEvent(room, type, content) {
    return JSON.stringify(await sdk.crypto.encryptRoomEvent(room, type, content));
  }

  async function encrypted(room, body) {
    return encryptEvent(room, messageType, { msgtype: 'm.text', body });
  }

  async function put(room, txn, ciphertext) {
    const result = await request('PUT', `${route(room)}/send/${encryptedType}/${encodeURIComponent(txn)}`, ciphertext);
    if (!result.event_id) throw new Error('Matrix did not return an event identity.');
    return result.event_id;
  }

  async function react(room, txn, event, key) {
    const result = await request('PUT', `${route(room)}/send/m.reaction/${encodeURIComponent(txn)}`,
      JSON.stringify({ 'm.relates_to': { rel_type: 'm.annotation', event_id: event, key } }));
    if (!result.event_id) throw new Error('Matrix did not confirm the reaction.');
    return result.event_id;
  }

  function redact(room, txn, event) {
    return request('PUT', `${route(room)}/redact/${encodeURIComponent(event)}/${encodeURIComponent(txn)}`, '{}');
  }

  async function joinPortal(room) {
    const joined = await request('GET', '/_matrix/client/v3/joined_rooms');
    if (!joined.joined_rooms.includes(room)) {
      const result = await request('POST', `${route(room)}/join`, '{}');
      if (result.room_id !== room) throw new Error('Wrong Matrix portal membership.');
    }
  }

  return { sync, applySync, liveState, officeEvent, verifyControl, roomRemovedFromSpace, recentMessages, joinPortal, encrypted, encryptEvent, put, react, redact,
    close: () => sdk.crypto?.engine.machine.close(), tokenHash: digest([who.user_id, who.device_id, token]) };
}

export function statusEvidence(config, room, event) {
  if (event.type !== bridgeStatus || event.sender !== config.bridgeBot || event.unsigned?.redacted_because) return;
  const relation = event.content?.['m.relates_to'];
  if (relation?.rel_type !== 'm.reference' || !relation.event_id) return;
  return { room, event: relation.event_id, status: event.content.status };
}

export function bridgeAudit(config) {
  const pool = new pg.Pool({ connectionString: config.auditDatabase, max: 2, connectionTimeoutMillis: 3_000, query_timeout: 3_000 });
  pool.on('error', () => {});

  async function verifiedTarget(room, target) {
    const { rows } = await pool.query(`SELECT login, owner, relay, room_kind, forum, supergroup, blocked
      FROM telegram_audit_portal WHERE room=$1 AND peer=$2 AND receiver=$3`, [room, target.peer, target.receiver]);
    const portal = rows[0];
    if (rows.length !== 1 || portal.login !== target.login || portal.owner !== config.owner ||
        portal.relay !== target.login || portal.forum || portal.blocked) return false;
    if (target.peer.startsWith('user:')) return portal.room_kind === 'dm';
    if (target.peer.startsWith('channel:')) return portal.room_kind === '' && portal.supergroup;
    return portal.room_kind === '';
  }

  async function source(room, target, event, waits = [0, 200, 400, 800, 1600]) {
    for (const wait of waits) {
      if (wait) await delay(wait);
      const { rows } = await pool.query(`SELECT sender_id, sender_mxid, ts_ms FROM telegram_audit_message
        WHERE room=$1 AND peer=$2 AND receiver=$3 AND event=$4`, [room, target.peer, target.receiver, event.event_id]);
      if (rows.length !== 1) continue;
      const mapped = rows[0];
      const sender = `@telegram_${mapped.sender_id}:${config.domain}`;
      if (!/^[1-9][0-9]{0,18}$/u.test(mapped.sender_id) || mapped.sender_mxid !== event.sender || sender !== event.sender) return;
      return { author: mapped.sender_id === target.login ? 'owner' : 'remote', ts: Number(mapped.ts_ms) };
    }
  }

  return { verifiedTarget, source, close: () => pool.end() };
}
