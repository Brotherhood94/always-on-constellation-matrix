import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { requestTelegramService } from './telegram-office.mjs';

const requestField = 'org.matrixconstellation.telegram.request';
const event = /^$[^\s\x00-\x1f]{1,254}$/u;
const hash = value => createHash('sha256').update(value).digest('hex');
const html = value => value.replace(/[&<>]/gu, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[char]);

export function verifyTelegramControlState(state, room) {
  const settings = (type) => state.find(entry => entry.type === type && entry.state_key === '')?.content;
  const members = state.filter(entry => entry.type === 'm.room.member' && ['join', 'invite'].includes(entry.content?.membership));
  return settings('m.room.create')?.['m.federate'] === false && settings('m.room.join_rules')?.join_rule === 'invite' &&
    settings('m.room.encryption')?.algorithm === 'm.megolm.v1.aes-sha2' && !settings('m.room.tombstone') &&
    members.length === 3 && members.every(entry => room.members.includes(entry.state_key) && entry.content.membership === 'join');
}

export async function createTelegramTools({ state, serverName, userId, db, sendEncrypted, getClient, z }) {
  let control;
  try {
    control = JSON.parse(await readFile(join(state, 'telegram-control.json'), 'utf8'));
  } catch (error) {
    if (error.code === 'ENOENT') return { register() {} };
    throw error;
  }
  const agent = '@telegram-agent:' + serverName;
  if (!/^![^\s\x00-\x1f]{1,254}$/u.test(control.room) ||
      !/^@[a-z0-9._=/-]+:[^\s]+$/u.test(control.owner) || !control.owner.endsWith(':' + serverName) ||
      [agent, userId].includes(control.owner)) throw new Error('Invalid Telegram Agent configuration.');
  control.members = [control.owner, userId, agent];
  db.exec(`
    CREATE TABLE IF NOT EXISTS telegram_requests (
      request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, event_id TEXT UNIQUE
    );
  `);
  if (!db.prepare('PRAGMA table_info(telegram_requests)').all().some(column => column.name === 'room_id')) {
    db.exec('ALTER TABLE telegram_requests ADD COLUMN room_id TEXT');
  }
  const put = db.prepare('INSERT OR IGNORE INTO telegram_requests(request_id, fingerprint) VALUES (?, ?)');
  const get = db.prepare('SELECT * FROM telegram_requests WHERE request_id=?');
  const saved = db.prepare('UPDATE telegram_requests SET event_id=?, room_id=? WHERE request_id=? AND (event_id IS NULL OR event_id=?)');

  async function queue(requestId, payload, response, failure) {
    const fingerprint = hash(JSON.stringify(payload));
    put.run(requestId, fingerprint);
    const prior = get.get(requestId);
    if (prior.fingerprint !== fingerprint) return failure('This request ID belongs to a different Telegram request.');
    let eventId = prior.event_id;
    if (!eventId && control.local === true) {
      try {
        const result = await requestTelegramService('/telegram-dot/requests.sock', { id: requestId, ...payload }, 15000);
        if (result.status === 'conflict') return failure('This request ID belongs to a different Telegram request.');
        if (result.status !== 'queued' || result.room !== control.room || !event.test(result.event)) {
          return failure('Telegram Bridge is unavailable. Retry with the same request ID.');
        }
        eventId = result.event;
      } catch {
        return failure('The Telegram request could not be confirmed. Retry with the same request ID.');
      }
    } else if (!eventId) {
      const client = getClient();
      if (!client) return failure('Dot is connecting to Matrix. Retry with the same request ID.');
      try {
        const state = await client.getRoomState(control.room);
        if (!verifyTelegramControlState(state, control)) return failure('Telegram Agent membership or encryption could not be verified.');
        const requestText = payload.action === 'compose' ? 'Prepare a message' : 'Request an update';
        const description = `: ${requestText} for ${payload.destination} (${payload.chat_type}).`;
        eventId = await sendEncrypted(client, control.room, hash('dot-telegram:' + requestId), {
          msgtype: 'm.text', body: '@telegram-agent' + description,
          format: 'org.matrix.custom.html',
          formatted_body: '<a href="https://matrix.to/#/' + encodeURIComponent(agent) + '">telegram-agent</a>' + html(description),
          'm.mentions': { user_ids: [agent] }, [requestField]: payload,
        });
      } catch {
        return failure('The Telegram request could not be confirmed. Retry with the same request ID.');
      }
    }
    if (!prior.event_id) saved.run(eventId, control.room, requestId, eventId);
    const room = prior.room_id ?? (prior.event_id ? control.legacy : undefined) ?? control.room;
    return response({ request_id: requestId, status: 'requested_in_telegram_control',
      request_url: `https://matrix.to/#/${encodeURIComponent(room)}/${encodeURIComponent(eventId)}` });
  }

  return {
    register(server, requestId, response, failure) {
      const shared = {
        request_id: requestId.describe('Use the same request ID and input if retrying.'),
        destination: z.string().trim().min(1).max(120).describe('The exact approved Telegram chat name or binding ID.'),
        chat_type: z.enum(['direct', 'group']).describe('Ask the operator if private versus group is unclear.'),
      };
      const annotations = { idempotentHint: true, destructiveHint: false, openWorldHint: false };
      server.registerTool('prepare_telegram_message', {
        description: 'Request a private preview of an exact new or reply message in Telegram Bridge. To request delivery or a result in Office, call publish_telegram_task.',
        inputSchema: { ...shared,
          text: z.string().min(1).refine(value => !!value.trim() && Buffer.byteLength(value) <= 3500),
          source_event: z.string().regex(event).optional().describe('Optional Matrix event ID of a received message in this same connected chat.'),
        }, annotations,
      }, ({ request_id, ...input }) => queue(request_id, { action: 'compose', ...input }, response, failure));
      server.registerTool('request_telegram_update', {
        description: 'Ask Telegram Agent for a read-only update from one connected Telegram chat. The result is posted only in Telegram Bridge in Element and mentions the operator. This tool does not return chat contents.',
        inputSchema: { ...shared, question: z.string().trim().min(1).refine(value => Buffer.byteLength(value) <= 1000) },
        annotations,
      }, ({ request_id, ...input }) => queue(request_id, { action: 'update', ...input }, response, failure));
    },
  };
}
