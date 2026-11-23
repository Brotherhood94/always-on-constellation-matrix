import { randomUUID } from 'node:crypto';
import { existsSync } from 'node:fs';
import { readFile, writeFile } from 'node:fs/promises';
import { basename, join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { TelegramStore, controlCommand, digest, validateBinding } from './policy.mjs';
import { bridgeAudit, matrixConnection, statusEvidence } from './matrix.mjs';
import { draftWithInstance } from './acp.mjs';

const directory = '/state';
const configFile = join(directory, 'config.json');
const settings = JSON.parse(await readFile(configFile, 'utf8'));
const owner = `@${settings.owner.split(':')[0].slice(1)}:${settings.domain}`;
if (owner !== settings.owner || settings.agent !== `@telegram-agent:${settings.domain}` ||
    settings.bridgeBot !== `@telegrambot:${settings.domain}` || settings.matrixUrl !== 'http://synapse:8008') {
  throw new Error('Invalid local Matrix identities.');
}
const store = new TelegramStore(join(directory, 'telegram.sqlite3'));
const audit = bridgeAudit(settings);
const [action, argument] = process.argv.slice(2);
const matrix = await matrixConnection(settings, directory, { crypto: !action });
const pausedFile = () => existsSync(join(directory, 'paused'));
const date = new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/Rome', dateStyle: 'short', timeStyle: 'long' });

async function liveBinding(room, target) {
  if (!(await audit.verifiedTarget(room, target))) return false;
  const state = await matrix.liveState(room);
  if (!state.encrypted || !state.private || state.tombstone) return false;
  const known = new Set([settings.owner, settings.agent, settings.bridgeBot]);
  const ghost = new RegExp(`^@telegram_[1-9][0-9]{0,18}:${settings.domain.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`, 'u');
  if (state.members.some(([member]) => !known.has(member) && !ghost.test(member))) return false;
  if (![...known].every(member => state.members.some(([id, status]) => id === member && status === 'join'))) return false;
  return state.audience;
}

async function waitForMatrixSync() {
  // Require a sync that started after the portal join, and applied its whole batch.
  const request = randomUUID();
  store.set('bind_sync_request', request);
  const timeout = AbortSignal.timeout(30_000);
  while (!timeout.aborted) {
    if (store.setting('bind_sync_completed') === request) return;
    await delay(250);
  }
  throw new Error('The connector did not finish a Matrix sync after joining. Check that it is running, then retry bind.');
}

if (action) {
  try {
    if (['bind', 'rotate'].includes(action)) {
      if (!argument || basename(argument) !== argument || !/^binding-[a-z0-9-]+\.json$/u.test(argument)) throw new Error('Use a private binding file.');
      if (!(await matrix.verifyControl())) throw new Error('The private Telegram Control room is not ready.');
      const binding = JSON.parse(await readFile(join(directory, argument), 'utf8'));
      validateBinding({ ...binding, audience: '0'.repeat(64) }, settings.controlRoom);
      if (!(await audit.verifiedTarget(binding.room, binding))) throw new Error('The bridge mapping or relay could not be verified.');
      await matrix.joinPortal(binding.room);
      if (action === 'bind') await waitForMatrixSync();
      const audience = await liveBinding(binding.room, binding);
      if (!audience) throw new Error('The portal, encrypted audience, or configured relay could not be verified.');
      if (!(await matrix.verifyControl())) throw new Error('The private Telegram Control room is not ready.');
      const method = action === 'rotate' ? 'reset' : 'bind';
      store[method]({ ...binding, audience, operator: settings.owner }, settings.controlRoom, Date.now());
      console.log(action === 'rotate' ? 'Telegram binding rotated while paused.' : 'Telegram binding verified and stored.');
    } else if (action === 'pause') {
      store.pause(argument);
      console.log('Telegram binding paused.');
    } else if (action === 'resume') {
      if (pausedFile()) throw new Error('Remove the external pause only after reconciling delivery.');
      if (!matrix.tokenHash || matrix.tokenHash !== store.setting('identity')) throw new Error('Verify the original Matrix identity.');
      if (!(await matrix.verifyControl())) throw new Error('Control is not ready.');
      store.set('paused', false);
      console.log('Global pause cleared. Individual bindings must be revalidated.');
    } else if (action === 'probe') {
      const fixture = [{ author: 'remote', body: 'Connectivity test only. Answer with TELEGRAM_READY.' }];
      let session;
      const first = await draftWithInstance(settings.engine, null, fixture, (id) => { session = id; });
      const second = await draftWithInstance(settings.engine, session, fixture, (id) => { session = id; });
      if (first !== 'TELEGRAM_READY' || second !== 'TELEGRAM_READY') throw new Error('Backend probe returned an unexpected response.');
      await writeFile(join(directory, 'backend-verified'), settings.engine.revision, { mode: 0o600 });
      console.log('Telegram backend generated a synthetic draft and loaded its saved ACP session.');
    } else if (action === 'report') {
      console.log(JSON.stringify({ paused: store.paused() || pausedFile(), bindings: store.bindings().map(row => ({
        id: row.id, mode: row.mode, enabled: Boolean(row.active), generation: row.generation,
        ...(row.active ? {} : { pauseReason: store.setting('pause_reason:' + row.id) ?? null }),
      })), lastSync: store.setting('last_sync') ?? null,
      open: store.all(`SELECT binding,id,state FROM draft WHERE state IN ('awaiting_approval','ready','publishing','matrix_accepted','unknown')`),
      interrupted: store.all(`SELECT binding, COUNT(*) AS count FROM work WHERE state IN ('interrupted','backend_failed') GROUP BY binding`),
    }, null, 2));
    } else throw new Error('Unknown connector operator command.');
  } finally {
    await audit.close();
  }
} else {
  try {
    await run();
  } finally {
    matrix.close();
  }
}

async function run() {
  if ((await readFile(join(directory, 'backend-verified'), 'utf8')).trim() !== settings.engine?.revision) {
    throw new Error('The configured engine has not passed the synthetic probe.');
  }
  if (store.setting('identity') && store.setting('identity') !== matrix.tokenHash) {
    throw new Error('Matrix device or access token changed. Publication is blocked pending reconciliation.');
  }
  store.set('identity', matrix.tokenHash);
  store.recover(Date.now());
  const catchup = 15 * 60_000;
  let worker;
  let lastGood = 0;
  let nextNotice = 0;
  let lastCleanup = 0;
  let stopping = false;
  for (const signal of ['SIGTERM', 'SIGINT']) process.once(signal, () => { stopping = true; });

  async function dispatch(room, event, encrypted, unreadable, initial) {
    const now = Date.now();
    const binding = store.inRoom(room);
    if (binding && encrypted && event.sender === settings.agent && event.unsigned?.transaction_id) {
      store.accepted(event.unsigned.transaction_id, room, event.event_id);
    }
    const delivery = binding && statusEvidence(settings, room, event);
    if (delivery) store.delivery(room, delivery.event, delivery.status, now);
    if (initial || (room !== settings.controlRoom && !binding?.active)) return;
    if (binding && ['m.room.tombstone', 'm.room.encryption', 'm.room.join_rules'].includes(event.type)) {
      store.pause(binding.id, 'portal_security_state_changed');
      return;
    }
    if (!encrypted || event.sender === settings.agent || event.sender === settings.bridgeBot || event.unsigned?.redacted_because) return;
    if (unreadable) {
      if (binding?.active && event.origin_server_ts >= binding.activated) store.pause(binding.id, 'message_decryption_failed');
      return;
    }
    if (room === settings.controlRoom) {
      const command = controlCommand({ room, sender: event.sender, content: event.content, encrypted }, settings);
      if (!command || now - event.origin_server_ts > catchup || now + 60_000 < event.origin_server_ts) return;
      if (await matrix.verifyControl()) store.command({ event: event.event_id, room, sender: event.sender, encrypted }, command, settings, now);
      return;
    }
    if (event.type !== 'm.room.message' || now - event.origin_server_ts > catchup || event.origin_server_ts < binding.activated) return;
    const body = event.content?.msgtype === 'm.text' && !event.content['m.new_content'] && !event.content['m.relates_to']?.rel_type
      ? event.content.body : null;
    if (event.sender === settings.owner) {
      store.record({ event: event.event_id, room, author: 'owner', ts: event.origin_server_ts, body }, now);
      return;
    }
    const target = JSON.parse(binding.target);
    const evidence = await audit.source(room, target, event);
    if (!evidence) {
      // An unmatched recent portal message could be newer human context. Pause instead of approving stale output.
      store.pause(binding.id, 'unverified_telegram_message');
      return;
    }
    if (await liveBinding(room, target) !== target.audience) {
      store.pause(binding.id, 'portal_or_audience_changed');
      return;
    }
    store.record({ event: event.event_id, room, ...evidence, body }, now);
  }

  async function handleSync() {
    const previous = store.setting('sync');
    const bindRequest = store.setting('bind_sync_request');
    const batch = await matrix.sync(previous);
    if (typeof batch.next_batch !== 'string' || !batch.next_batch) throw new Error('Missing Matrix sync cursor.');
    for (const binding of store.bindings()) {
      if (binding.active) {
        if (batch.rooms?.leave?.[binding.room]) store.pause(binding.id, 'portal_membership_lost');
        else if (!previous || batch.rooms?.join?.[binding.room]?.timeline?.limited) store.pause(binding.id, 'matrix_sync_gap');
      }
    }
    if (previous && (batch.rooms?.join?.[settings.controlRoom]?.timeline?.limited || batch.rooms?.leave?.[settings.controlRoom])) {
      store.pause(undefined, 'control_room_sync_gap');
    }
    await matrix.applySync(batch, (room, event, encrypted, unreadable) => dispatch(room, event, encrypted, unreadable, !previous));
    lastGood = Date.now();
    store.atomic(() => {
      store.set('sync', batch.next_batch);
      store.set('last_sync', new Date(lastGood).toISOString());
      if (bindRequest && store.setting('bind_sync_completed') !== bindRequest) store.set('bind_sync_completed', bindRequest);
    });
  }

  async function processWork(work) {
    const binding = store.binding(work.binding);
    const target = JSON.parse(binding.target);
    if (pausedFile() || !settings.engine || await liveBinding(binding.room, target) !== target.audience ||
        !(await matrix.verifyControl()) || pausedFile() || !store.current(work, Date.now())) {
      store.finish(work, 'stale');
      return;
    }
    try {
      if (binding.session && binding.engine !== settings.engine.revision) throw new Error('Engine changed.');
      const draft = await draftWithInstance(settings.engine, binding.session, store.history(work),
        (session) => store.session(work, session, settings.engine.revision));
      if (pausedFile()) store.finish(work, 'cancelled');
      else store.saveCandidate(work, draft, Date.now());
    } catch {
      store.finish(work, 'backend_failed');
      store.pause(work.binding, 'backend_turn_failed');
      console.warn('One Telegram backend turn failed. Its binding was paused.');
    }
  }

  async function notify(draft) {
    const awaiting = draft.state === 'awaiting_approval';
    const content = awaiting ? [
      `Draft for ${draft.binding}`, `Source: https://matrix.to/#/${draft.room}/${draft.source}`, '', draft.body, '',
      `Expires: ${date.format(draft.until)}`, `telegram approve ${draft.id} ${draft.version}`,
      `telegram reject ${draft.id} ${draft.version}`,
    ].join('\n') : `${draft.binding}: ${draft.state.replaceAll('_', ' ')}. Draft ${draft.id}.`;
    const prepared = await matrix.encrypted(settings.controlRoom, content);
    if (!(await matrix.verifyControl())) throw new Error('Control room changed.');
    await matrix.put(settings.controlRoom, `notice-${digest([draft.id, draft.state])}`, prepared);
    store.notified(draft.id, draft.state);
  }

  async function publish() {
    const draft = store.nextSend(Date.now());
    if (!draft || pausedFile() || store.paused() || Date.now() - lastGood > 15_000) return;
    const target = JSON.parse(draft.target);
    if (await liveBinding(draft.room, target) !== target.audience || !(await matrix.verifyControl())) {
      store.pause(draft.binding, 'portal_or_control_room_changed');
      return;
    }
    const ciphertext = draft.ciphertext ?? await matrix.encrypted(draft.room, draft.body);
    if (await liveBinding(draft.room, target) !== target.audience) {
      store.pause(draft.binding, 'portal_or_audience_changed');
      return;
    }
    if (Date.now() - lastGood > 15_000 || pausedFile() || !store.prepareSend(draft.id, ciphertext, Date.now())) return;
    try {
      const event = await matrix.put(draft.room, draft.txn, ciphertext);
      store.accepted(draft.txn, draft.room, event);
    } catch (error) {
      if (error.status === 429 && error.code === 'M_LIMIT_EXCEEDED') store.retry(draft.id, Date.now());
      else store.sendError(draft.id);
    }
  }

  while (!stopping) {
    try {
      await handleSync();
      const now = Date.now();
      store.expire(now);
      if (now - lastCleanup > 60_000) {
        store.retain(now);
        lastCleanup = now;
      }
      if (pausedFile() && !store.paused()) store.pause();
      const notice = now >= nextNotice && store.pendingNotice();
      if (notice && await matrix.verifyControl()) {
        nextNotice = now + 5_000;
        await notify(notice);
      }
      await publish();
      if (!worker && !pausedFile() && !store.paused()) {
        const work = store.nextWork(Date.now());
        if (work) worker = processWork(work).catch(() => {
          store.finish(work, 'backend_failed');
          store.pause(work.binding, 'backend_turn_failed');
        }).finally(() => { worker = null; });
      }
    } catch {
      lastGood = 0;
      console.warn('Telegram sync or delivery check failed. Retrying without sending.');
      await delay(4_000);
    }
  }
  await writeFile(join(directory, 'last-stop'), new Date().toISOString(), { mode: 0o600 });
  if (worker) await worker;
  await audit.close();
}
