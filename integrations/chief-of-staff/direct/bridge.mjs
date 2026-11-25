import { readFile, readdir, unlink, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { setTimeout as delay } from 'node:timers/promises';
import matrix from '@vector-im/matrix-bot-sdk';
import { mentionMessage } from '../../../agents/connector/matrix-format.mjs';
import { openRoomClient, sendEncrypted } from '../../../agents/connector/matrix-session.mjs';
import { observationMessage, parseObservation, validateRoomId } from '../observation.mjs';
import { contextRequest, interaction, writeInteraction } from './evidence.mjs';
import { isTeamReview, reportId, workshopObservation } from './review.mjs';
import { createCatalog, gapKey } from '../../../agents/connector/agent-catalog.mjs';
import { reviewTeam } from './run-review.mjs';
import { capabilityReview, confirmedGap } from './capability-review.mjs';
import { createRosterApproval } from './roster-approval.mjs';

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
  CREATE TABLE IF NOT EXISTS review_requests (
    event TEXT PRIMARY KEY, raw TEXT, response TEXT, sent INTEGER NOT NULL DEFAULT 0, retry INTEGER NOT NULL DEFAULT 0
  );
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
  const events = await client.getRoomState(destination);
  const rules = events.find(event => event.type === 'm.room.join_rules');
  const members = events.filter(event => event.type === 'm.room.member' &&
    ['invite', 'join'].includes(event.content.membership));
  if (rules?.content.join_rule !== 'invite' || members.length !== 2 || ![owner, user].every(id =>
    members.some(event => event.state_key === id && event.content.membership === 'join'))) {
    throw new Error('The Chief of Staff reporting room must be invite-only with the owner and bot joined');
  }
}

await checkDestination();
const roster = process.env.ROSTER_MANAGEMENT === 'true' ? createRosterApproval({
  db, client, room: destination, owner, checkDestination,
  currentAgents: () => catalog.fresh() ? catalog.current().agents : null,
  configureCommand: process.env.ROSTER_CONFIGURE_COMMAND,
}) : null;
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

async function fillGap(room, previous, previousBatch) {
  const events = [];
  const visited = new Set();
  let cursor = previousBatch;
  while (cursor && cursor !== previous && !visited.has(cursor)) {
    visited.add(cursor);
    const response = await client.doRequest('GET',
      '/_matrix/client/v3/rooms/' + encodeURIComponent(room) + '/messages',
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
  if (since && timeline?.limited && timeline.prev_batch) events.push(...await fillGap(source, since, timeline.prev_batch));
  const directTimeline = joined[destination]?.timeline;
  let directEvents = directTimeline?.events || [];
  if (roster && since && directTimeline?.limited && directTimeline.prev_batch) {
    const backfill = await fillGap(destination, since, directTimeline.prev_batch);
    directEvents = [...backfill.reverse(), ...directEvents];
  }
  db.exec('BEGIN');
  try {
    for (const event of events) {
      if (event.type === 'm.room.encrypted' && event.event_id && event.origin_server_ts >= start &&
          !event.unsigned?.redacted_because) {
        queue.run(event.event_id, event.origin_server_ts, JSON.stringify(event));
      }
    }
    const addReview = db.prepare('INSERT OR IGNORE INTO review_requests (event,raw) VALUES (?,?)');
    for (const event of directEvents) {
      if (event.type === 'm.room.encrypted' && event.sender === owner && event.event_id &&
          event.origin_server_ts >= start && !event.unsigned?.redacted_because) {
        addReview.run(event.event_id, JSON.stringify(event));
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

async function drainReviewRequest() {
  const row = db.prepare('SELECT * FROM review_requests WHERE sent=0 AND retry <= ? ORDER BY rowid LIMIT 1').get(Date.now());
  if (!row) return;
  await checkDestination();
  let response = row.response;
  const finish = db.prepare('UPDATE review_requests SET sent=1,raw=NULL WHERE event=?');
  if (!response) {
    let event;
    try {
      event = (await client.crypto.decryptRoomEvent(
        new matrix.EncryptedRoomEvent(JSON.parse(row.raw)), destination)).raw;
    } catch (error) {
      if (error.code !== 'GenericFailure') throw error;
      db.prepare('UPDATE review_requests SET retry=? WHERE event=?').run(Date.now() + 30000, row.event);
      return;
    }
    const handled = roster ? await roster.accept(event, JSON.parse(row.raw)) : null;
    if (handled !== null) {
      if (!handled) {
        finish.run(row.event);
        return;
      }
      response = handled;
    } else if (!isTeamReview(event)) {
      finish.run(row.event);
      return;
    } else {
      try {
        response = await reviewTeam();
      } catch {
        response = 'Chief of Staff could not complete the review. Send “review team” again after checking Workshop and Context Intelligence.';
      }
    }
    db.prepare('UPDATE review_requests SET response=?,raw=NULL WHERE event=?').run(response, row.event);
  }
  await checkDestination();
  await sendEncrypted(client, destination, 'cos-review-' + reportId(row.event), mentionMessage(response));
  finish.run(row.event);
}

async function drainReports() {
  const outbox = '/direct/outbox';
  const names = (await readdir(outbox)).filter(name => /^[0-9a-f]{64}\.json$/.test(name)).sort();
  for (const name of names.slice(0, 5)) {
    const path = join(outbox, name);
    const job = JSON.parse(await readFile(path, 'utf8'));
    const id = reportId(job.threads);
    if (job.room !== source || name !== id + '.json') throw new Error('Unexpected review file');
    const note = job.workshop ? workshopObservation(job.response, job.threads, job.workshop) :
      parseObservation(job.response, job.threads);
    if (!note) throw new Error('The report has no observation');
    let receipt = readReport.get(id);
    if (!receipt) {
      await checkDestination();
      const content = observationMessage(note);
      const target = note.agent_key ? `Persona draft for ${note.agent_key}. ` : '';
      const identity = `${target}Observation ID cos-direct-${id}`;
      content.plain += '\n\n' + identity;
      content.rich += '\n\n' + identity;
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

let review;
let rosterWork;
for (;;) {
  try {
    await catalog.refresh();
    await sync();
    await drainEvidence();
    review ??= drainReviewRequest().catch(() => console.warn('Chief of Staff will retry the private DM request.'))
      .finally(() => { review = undefined; });
    if (roster) rosterWork ??= roster.tick().catch(() => console.warn('Chief of Staff will retry the roster operation.'))
      .finally(() => { rosterWork = undefined; });
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
