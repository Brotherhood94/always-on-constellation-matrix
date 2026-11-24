import { readFile, readdir, unlink, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { setTimeout as delay } from 'node:timers/promises';
import matrix from '@vector-im/matrix-bot-sdk';
import { mentionMessage } from '../../../agents/connector/matrix-format.mjs';
import { openRoomClient, sendEncrypted } from '../../../agents/connector/matrix-session.mjs';
import { observationMessage, parseObservation, validateRoomId } from '../observation.mjs';
import { contextRequest, interaction, writeInteraction } from './evidence.mjs';
import { reportId } from './review.mjs';
import { createCatalog, gapKey } from '../../../agents/connector/agent-catalog.mjs';
import { capabilityReview, confirmedGap } from './capability-review.mjs';

const { MATRIX_URL: url, MATRIX_SERVER_NAME: server, MATRIX_OWNER: owner } = process.env;
const state = '/publisher';
const user = `@chief-of-staff:${server}`;
const source = validateRoomId((await readFile('/direct/agents-room-id', 'utf8')).trim());
const destination = validateRoomId((await readFile(join(state, 'room-id'), 'utf8')).trim());
const writer = (await readFile('/writer-token', 'utf8')).trim();
if (!url || !server || !owner || owner === user || source === destination || writer.length < 32) {
  throw new Error('Invalid Chief of Staff room or Context Intelligence configuration');
}

const db = new DatabaseSync('/direct/reader.sqlite3');
const catalog = createCatalog(server, process.env.MATRIX_CATALOG_PATH);
const capabilityGaps = capabilityReview(db, source, owner);
db.exec(`
  PRAGMA journal_mode = WAL;
  PRAGMA synchronous = FULL;
  CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS pending (
    event_id TEXT PRIMARY KEY, stamp INTEGER NOT NULL, raw TEXT, evidence TEXT, retry INTEGER NOT NULL DEFAULT 0
  );
  CREATE INDEX IF NOT EXISTS pending_retry ON pending(retry, stamp);
  CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY, event_id TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0);
`);
db.prepare("INSERT OR IGNORE INTO settings VALUES ('start', ?)").run(String(Date.now()));
const start = Number(db.prepare("SELECT value FROM settings WHERE key = 'start'").get().value);
const queue = db.prepare('INSERT OR IGNORE INTO pending (event_id, stamp, raw) VALUES (?, ?, ?)');
const next = db.prepare('SELECT * FROM pending WHERE retry <= ? AND (raw IS NOT NULL OR evidence IS NOT NULL) ORDER BY retry, stamp LIMIT 40');
const saveEvidence = db.prepare('UPDATE pending SET raw = NULL, evidence = ? WHERE event_id = ?');
const retry = db.prepare('UPDATE pending SET retry = ? WHERE event_id = ?');
const complete = db.prepare('UPDATE pending SET raw = NULL, evidence = NULL WHERE event_id = ?');
const writeReport = db.prepare('INSERT INTO reports (id, event_id) VALUES (?, ?)');
const readReport = db.prepare('SELECT * FROM reports WHERE id = ?');
const archiveReport = db.prepare('UPDATE reports SET archived = 1 WHERE id = ?');

const client = await openRoomClient({
  url, state, roomId: destination, userId: user, deviceId: 'MATRIXCHIEFOFSTAFF', name: 'Chief of Staff',
});

async function checkDestination() {
  const rules = await client.getRoomStateEventContent(destination, 'm.room.join_rules', '');
  const members = await client.getJoinedRoomMembers(destination);
  if (rules.join_rule !== 'invite' || members.length !== 2 || !members.includes(owner) || !members.includes(user)) {
    throw new Error('The Chief of Staff reporting room must be invite-only with the owner and bot joined');
  }
}

await checkDestination();
while (!(await client.getJoinedRooms()).includes(source)) {
  try {
    await client.joinRoom(source);
  } catch (error) {
    if (!['M_FORBIDDEN', 'M_NOT_FOUND'].includes(error.body?.errcode)) throw error;
    console.log('Waiting for the Chief of Staff invitation to Agents.');
    await delay(15000);
  }
}
const encryption = await client.getRoomStateEventContent(source, 'm.room.encryption', '');
if (encryption.algorithm !== 'm.megolm.v1.aes-sha2') throw new Error('The Agents room must be encrypted');
await client.crypto.onRoomJoin(source);
const rooms = [source, destination];
const filter = JSON.stringify({
  presence: { not_types: ['*'] },
  room: {
    rooms,
    timeline: { limit: 100, types: ['m.room.encrypted', 'm.room.member', 'm.room.encryption'] },
    ephemeral: { not_types: ['*'] },
  },
});
console.log('Direct Matrix reading is active for Agents.');

async function fillGap(previous, previousBatch) {
  const events = [];
  const visited = new Set();
  let cursor = previousBatch;
  while (cursor && cursor !== previous && !visited.has(cursor)) {
    visited.add(cursor);
    const response = await client.doRequest('GET',
      '/_matrix/client/v3/rooms/' + encodeURIComponent(source) + '/messages',
      { from: cursor, to: previous, dir: 'b', limit: 100,
        filter: JSON.stringify({ types: ['m.room.encrypted'] }) }, null, 15000);
    events.push(...(response.chunk || []));
    cursor = response.end;
  }
  if (cursor && cursor !== previous) throw new Error('The Agents timeline returned a repeated paging token');
  return events;
}

async function sync() {
  const since = db.prepare("SELECT value FROM settings WHERE key = 'since'").get()?.value;
  const result = await client.doRequest('GET', '/_matrix/client/v3/sync',
    { timeout: since ? 10000 : 0, filter, ...(since && { since }) }, null, 22000);
  if (!result.next_batch) throw new Error('Matrix sync returned no cursor');
  await client.crypto.updateSyncData(result.to_device?.events || [], result.device_one_time_keys_count || {},
    result.device_unused_fallback_key_types || [], result.device_lists?.changed || [], result.device_lists?.left || []);
  const joined = result.rooms?.join || {};
  for (const id of rooms) {
    const stateDelta = [...(joined[id]?.state?.events || []), ...(joined[id]?.timeline?.events || [])];
    for (const event of stateDelta) {
      if (event.type === 'm.room.member' || event.type === 'm.room.encryption') {
        await client.crypto.onRoomEvent(id, event);
      }
    }
  }
  const timeline = joined[source]?.timeline;
  const events = [...(timeline?.events || [])];
  if (since && timeline?.limited && timeline.prev_batch) events.push(...await fillGap(since, timeline.prev_batch));
  db.exec('BEGIN');
  try {
    for (const event of events) {
      if (event.type === 'm.room.encrypted' && event.event_id && event.origin_server_ts >= start &&
          !event.unsigned?.redacted_because) {
        queue.run(event.event_id, event.origin_server_ts, JSON.stringify(event));
      }
    }
    db.prepare("INSERT OR REPLACE INTO settings VALUES ('since', ?)").run(result.next_batch);
    db.exec('COMMIT');
  } catch (error) {
    db.exec('ROLLBACK');
    throw error;
  }
  await writeFile('/tmp/direct-ready', 'ready');
}

async function drainEvidence() {
  for (const row of next.all(Date.now())) {
    let entry;
    if (row.evidence) {
      entry = JSON.parse(row.evidence);
    } else {
      const raw = JSON.parse(row.raw);
      try {
        const clear = (await client.crypto.decryptRoomEvent(new matrix.EncryptedRoomEvent(raw), source)).raw;
        entry = interaction(source, clear, user, owner);
        if (entry && clear.content?.[gapKey] && catalog.configured) {
          if (!catalog.fresh()) {
            retry.run(Date.now() + 30000, row.event_id);
            continue;
          }
          const gap = confirmedGap(clear, user, catalog.current());
          if (gap) entry.capability_gap = gap;
        }
      } catch (error) {
        if (error.code !== 'GenericFailure') throw error;
        retry.run(Date.now() + 30000, row.event_id);
        continue;
      }
      if (!entry) {
        complete.run(row.event_id);
        continue;
      }
      saveEvidence.run(JSON.stringify(entry), row.event_id);
    }
    await writeInteraction(writer, entry);
    if (entry.capability_gap) capabilityGaps.collect(entry);
    complete.run(row.event_id);
  }
}

async function drainReports() {
  const outbox = '/direct/outbox';
  const names = (await readdir(outbox)).filter(name => /^[0-9a-f]{64}\.json$/.test(name)).sort();
  for (const name of names.slice(0, 5)) {
    const path = join(outbox, name);
    const job = JSON.parse(await readFile(path, 'utf8'));
    const id = reportId(job.threads);
    if (job.room !== source || name !== id + '.json') throw new Error('Unexpected review file');
    const note = parseObservation(job.response, job.threads);
    if (!note) throw new Error('The report has no observation');
    let receipt = readReport.get(id);
    if (!receipt) {
      await checkDestination();
      const content = observationMessage(note);
      const eventId = await sendEncrypted(client, destination, 'cos-direct-' + id,
        mentionMessage(content.plain, undefined, content.rich));
      writeReport.run(id, eventId);
      receipt = readReport.get(id);
    }
    if (!receipt.archived) {
      const { recorded_by: _author, ...observation } = note;
      await contextRequest('/events', writer, {
        event: 'observation:recorded',
        workspace: 'agents-team',
        idempotency_key: 'cos-direct-' + id,
        data: {
          session_id: 'cos-direct-' + id,
          timestamp: job.created_at,
          observation: { ...observation, observation_id: 'cos-direct-' + id, matrix_event_id: receipt.event_id },
        },
      });
      archiveReport.run(id);
    }
    await unlink(path);
    console.log('Chief of Staff observation delivered and accepted by Context Intelligence.');
  }
}

for (;;) {
  try {
    await catalog.refresh();
    await sync();
    await drainEvidence();
    if (catalog.fresh()) capabilityGaps.plan(catalog.current());
    for (const job of capabilityGaps.pending()) {
      const { content, observation } = JSON.parse(job.data);
      let id = job.matrix_event;
      if (!id) {
        await checkDestination();
        id = await sendEncrypted(client, destination, 'cos-gap-' + job.id, content);
        capabilityGaps.sent(job.id, id);
      }
      await contextRequest('/events', writer, {
        event: 'observation:recorded', workspace: 'agents-team', idempotency_key: observation.observation_id,
        data: { session_id: observation.observation_id, timestamp: new Date(job.created).toISOString(),
          observation: { ...observation, matrix_event_id: id } },
      });
      capabilityGaps.archived(job.id);
    }
    await drainReports();
  } catch (error) {
    console.warn('Chief of Staff will retry:', error.body?.errcode || error.code || error.message);
    await delay(5000);
  }
}
