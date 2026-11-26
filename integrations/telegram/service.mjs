import { randomUUID } from 'node:crypto';
import { existsSync } from 'node:fs';
import { readFile, writeFile } from 'node:fs/promises';
import { basename, join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { TelegramStore, controlCommand, digest, pollStart, pollEnd, reviewAnswer, validateBinding } from './policy.mjs';
import { bridgeAudit, matrixConnection, statusEvidence } from './matrix.mjs';
import { draftWithInstance, runDelegatedWithInstance, summarizeWithInstance } from './acp.mjs';
import { addressedRequest, controlThread, historyRange, isDirect, matrixLink, resolveDestination, updateReply } from './control.mjs';
import { createTelegramDelegation, listenTelegramSocket } from './delegation.mjs';
import { requestTelegramService } from '../../agents/connector/telegram-office.mjs';

const directory = '/state';
const configFile = join(directory, 'config.json');
const settings = JSON.parse(await readFile(configFile, 'utf8'));
const owner = `@${settings.owner.split(':')[0].slice(1)}:${settings.domain}`;
if (owner !== settings.owner || settings.agent !== `@telegram-agent:${settings.domain}` ||
    (settings.dot !== undefined && (settings.dot !== `@dot:${settings.domain}` || settings.dot === owner)) ||
    settings.bridgeBot !== `@telegrambot:${settings.domain}` || settings.matrixUrl !== 'http://synapse:8008') {
  throw new Error('Invalid local Matrix identities.');
}
const store = new TelegramStore(join(directory, 'telegram.sqlite3'));
const audit = bridgeAudit(settings);
const [action, argument] = process.argv.slice(2);
const matrix = await matrixConnection(settings, directory, { crypto: !action });
const pausedFile = () => existsSync(join(directory, 'paused'));
const date = new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/Rome', dateStyle: 'short', timeStyle: 'long' });
const pollText = 'org.matrix.msc1767.text';

async function liveBinding(room, target) {
  if (!(await audit.verifiedTarget(room, target)) ||
      (settings.space && await matrix.roomRemovedFromSpace(settings.space, room))) return false;
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
      if (!(await matrix.verifyControl())) throw new Error('The private Telegram Agent room is not ready.');
      const binding = JSON.parse(await readFile(join(directory, argument), 'utf8'));
      validateBinding({ ...binding, audience: '0'.repeat(64) }, settings.controlRoom);
      if (!(await audit.verifiedTarget(binding.room, binding))) throw new Error('The bridge mapping or relay could not be verified.');
      await matrix.joinPortal(binding.room);
      if (action === 'bind') await waitForMatrixSync();
      const audience = await liveBinding(binding.room, binding);
      if (!audience) throw new Error('The portal, encrypted audience, or configured relay could not be verified.');
      if (!(await matrix.verifyControl())) throw new Error('The private Telegram Agent room is not ready.');
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
  const { TELEGRAM_HANDOFF_SOCKET, TELEGRAM_MCP_SOCKET, TELEGRAM_GUIDE_SOCKET } = process.env;
  const officeRoom = (await readFile(join(directory, 'office-room-id'), 'utf8').catch(error => {
    if (error.code !== 'ENOENT') throw error;
    return '';
  })).trim();
  const checkRoom = () => !pausedFile() && !store.paused() && matrix.verifyControl();
  const delegation = TELEGRAM_MCP_SOCKET && createTelegramDelegation({ store, settings: { ...settings, officeRoom }, checkRoom,
    guide: input => requestTelegramService(TELEGRAM_GUIDE_SOCKET, input),
    post: async (transaction, content) => {
      const ciphertext = await matrix.encryptEvent(settings.controlRoom, 'm.room.message', content);
      if (!await checkRoom()) throw new Error('Telegram Agent room changed');
      return matrix.put(settings.controlRoom, transaction, ciphertext);
    },
    runAgent: (grant, tool) => runDelegatedWithInstance(settings.engine, grant, tool),
    getBinding: async (room, peer) => {
      const binding = store.inRoom(room);
      const target = binding?.active && JSON.parse(binding.target);
      return target?.peer === peer && await liveBinding(room, target) === target.audience ? binding : null;
    },
  });
  const sockets = delegation ? [await listenTelegramSocket(TELEGRAM_MCP_SOCKET, delegation.tool)] : [];
  if (delegation && TELEGRAM_HANDOFF_SOCKET) sockets.push(await listenTelegramSocket(TELEGRAM_HANDOFF_SOCKET, delegation.office));

  async function dispatch(room, event, encrypted, unreadable, initial) {
    const now = Date.now();
    const binding = store.inRoom(room);
    if (binding && encrypted && event.sender === settings.agent && event.unsigned?.transaction_id) {
      store.accepted(event.unsigned.transaction_id, room, event.event_id);
    }
    const delivery = binding && statusEvidence(settings, room, event);
    if (delivery) store.delivery(room, delivery.event, delivery.status, now);
    if (room === settings.controlRoom && encrypted && !unreadable && event.sender === settings.agent &&
        event.type === pollStart && !event.unsigned?.redacted_because) {
      // Recover the mapping if Synapse accepted the poll before a process restart.
      const review = /^review-([a-f0-9-]{36})-([1-9][0-9]{0,4})$/u.exec(event.unsigned?.transaction_id);
      if (review) store.registerReview(review[1], Number(review[2]), event.event_id);
    }
    if (initial || (room !== settings.controlRoom && !binding?.active)) return;
    if (binding && ['m.room.tombstone', 'm.room.encryption', 'm.room.join_rules'].includes(event.type)) {
      store.pause(binding.id, 'portal_security_state_changed');
      return;
    }
    if (!encrypted || event.sender === settings.bridgeBot || event.unsigned?.redacted_because ||
        (event.sender === settings.agent && room !== settings.controlRoom)) return;
    if (unreadable) {
      if (binding?.active && event.origin_server_ts >= binding.activated) store.pause(binding.id, 'message_decryption_failed');
      return;
    }
    if (room === settings.controlRoom) {
      if (now - event.origin_server_ts > catchup || now + 60_000 < event.origin_server_ts) return;
      const command = controlCommand({ room, sender: event.sender, type: event.type, content: event.content, encrypted }, settings);
      if (command) {
        if (await matrix.verifyControl()) store.command({ event: event.event_id, room, sender: event.sender, encrypted }, command, settings, now);
      } else if (!await delegation?.direct(event)) {
        const request = addressedRequest(event, settings);
        const requireDot = event.sender === settings.dot || (event.sender === settings.owner && request?.action !== 'compose');
        if (request && !pausedFile() && await matrix.verifyControl({ requireDot })) {
          store.queueControl(event.event_id, request, now, { requireDot });
        }
      }
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

  async function processControl(request) {
    const requireDot = request.requires_dot !== 0;
    if (pausedFile() || store.paused() || !(await matrix.verifyControl({ requireDot }))) return;
    const input = JSON.parse(request.payload);
    const destinations = await Promise.all(store.bindings().filter(binding => binding.active).map(async binding => {
      const state = await matrix.liveState(binding.room).catch(error => {
        if (error.status !== 403 && error.status !== 404) throw error;
      });
      return { binding, state, name: state?.name || binding.id };
    }));
    const available = destinations.filter(({ state }) => state);
    const verified = input.verifiedBinding;
    const named = verified ? available.find(({ binding }) => binding.id === verified.id && binding.room === verified.room &&
      binding.generation === verified.generation && binding.revision === verified.revision) : resolveDestination(input, available);
    const context = input.thread && store.controlContext(input.thread);
    const result = (body, sources = [], binding, success = false) =>
      store.controlResult(request, JSON.stringify({ body, sources, success }), binding);
    if (verified && !named) return result('The selected chat changed. Verify its binding and start a new request.');
    let selected = named;
    if (context) {
      if (named && named.binding.id !== context.binding) {
        return result('This thread is already linked to another chat. Start a new request for the other chat.');
      }
      selected = available.find(({ binding }) => binding.id === context.binding &&
        binding.generation === context.generation && binding.revision === context.revision);
      if (!selected) return result('The chat linked to this thread has changed. Start a new request with the chat name.');
    }
    if (!selected) {
      const choices = available.map(({ binding, name }) =>
        `${name} (${isDirect(binding) ? 'private chat' : 'group'})`).join(', ');
      return result(`Specify one connected chat by name. Include “privato” or “private chat” for a direct chat.${choices ? '\nAvailable: ' + choices : ''}`);
    }
    const { binding, state, name } = selected;
    const target = JSON.parse(binding.target);
    if (await liveBinding(binding.room, target) !== target.audience) {
      return result('The portal or its membership changed. Verify the binding before requesting another update or draft.');
    }
    if (input.action === 'compose') {
      const recipient = `${name} (${isDirect(binding) ? 'private chat' : 'group'})`;
      if (pausedFile() || !(await matrix.verifyControl({ requireDot }))) return;
      if (!store.prepareControlDraft(request, binding, recipient, input.text, input.source, Date.now())) {
        result('The chat changed, cannot send from this binding, or the reply does not reference a received message in this chat.');
      }
      return;
    }

    const range = historyRange(input.question, request.requested);
    const history = await matrix.recentMessages(binding.room, range);
    const admitted = (await Promise.all(history.events.map(async event => {
      if (event.sender === settings.owner || event.sender === settings.agent) return { ...event, author: 'owner' };
      const source = await audit.source(binding.room, target, event, [0]);
      if (source) return { ...event, author: source.author };
    }))).filter(Boolean).reverse();
    if (pausedFile() || store.paused() || !(await matrix.verifyControl({ requireDot })) ||
        await liveBinding(binding.room, target) !== target.audience) return;
    if (input.action === 'reply') {
      const latest = admitted.at(-1);
      if (latest?.author !== 'remote') {
        return result('There is no latest verified incoming text message to reply to in this chat.', [], binding);
      }
      const previous = store.controlDraft(input.thread ?? request.event, binding.id, latest.event_id);
      if (previous && (previous.state !== 'awaiting_approval' || store.current(previous, Date.now()))) {
        const state = previous.state === 'awaiting_approval' ? 'A draft for this message is already waiting for your approval.'
          : 'A reply for this message has already been approved in this thread. Check its status before retrying.';
        const links = previous.review_event ? [{ label: 'Open the draft review', url: matrixLink(settings.controlRoom, previous.review_event) }] : [];
        return result(state, links, binding);
      }
      const contextMessages = admitted.slice(-8).map(({ author, body }) => ({
        author, body: body.replace(/^(?:>.*\n)+\n/u, '').slice(0, 1000),
      }));
      const text = await draftWithInstance(settings.engine, null, contextMessages, () => {}, input.question);
      if (pausedFile() || store.paused() || !(await matrix.verifyControl({ requireDot })) ||
          await liveBinding(binding.room, target) !== target.audience) return;
      const recipient = `${name} (${isDirect(binding) ? 'private chat' : 'group'})`;
      const evidence = { room: binding.room, event: latest.event_id };
      if (!store.prepareControlDraft(request, binding, recipient, text, latest.event_id, Date.now(), evidence)) {
        result('The chat changed while the reply was being prepared. Ask again to draft for the latest message.', [], binding);
      }
      return;
    }
    const coverage = `${name} · ${range.label} · ${admitted.length} readable text messages in the connected room.`;
    const partial = !history.complete || history.unreadable > 0 || admitted.length !== history.events.length;
    if (!admitted.length) return result(coverage + '\nNo verified text messages were available for this request.', [], binding, true);
    const messages = admitted.map((event, index) => ({
      number: index + 1,
      author: (state.names.get(event.sender) || event.sender).slice(0, 100),
      time: date.format(event.ts),
      body: event.body.replace(/^(?:>.*\n)+\n/u, '').slice(0, 1000),
    }));
    const answer = await summarizeWithInstance(settings.engine, input.question, messages);
    if (!answer) throw new Error('No summary returned.');
    const cited = [...new Set([...answer.matchAll(/\[(\d{1,2})\]/gu)].map(match => Number(match[1])))]
      .filter(number => number > 0 && number <= admitted.length);
    const numbers = cited.length ? cited.slice(0, 8) : messages.slice(-3).map(message => message.number);
    const sources = numbers.map(number => ({
      label: `[${number}] ${messages[number - 1].author} · ${messages[number - 1].time}`,
      url: matrixLink(binding.room, admitted[number - 1].event_id),
    }));
    const accessNote = partial ? ' Coverage is partial.' : '';
    if (!pausedFile() && !store.paused()) result(coverage + accessNote + '\n\n' + answer.slice(0, 3000), sources, binding, true);
  }

  async function publishControlResult(request) {
    if (!request) return;
    const requireDot = request.requires_dot !== 0;
    if (pausedFile() || store.paused() || !(await matrix.verifyControl({ requireDot }))) return;
    let { body, sources, success } = JSON.parse(request.result);
    if (request.binding) {
      const current = store.binding(request.binding);
      const target = current && JSON.parse(current.target);
      if (!current?.active || current.generation !== request.generation || current.revision !== request.revision ||
          await liveBinding(current.room, target) !== target.audience) {
        body = 'The selected chat changed before the update could be delivered. Request a new update.';
        sources = [];
        success = false;
      }
    }
    if (!(await matrix.verifyControl({ requireDot }))) return;
    const encrypted = await matrix.encryptEvent(settings.controlRoom, 'm.room.message',
      updateReply(requireDot ? settings : { ...settings, dot: undefined }, request.event, body, sources, request.thread_root ?? request.event));
    if (pausedFile() || store.paused() || !(await matrix.verifyControl({ requireDot }))) return;
    await matrix.put(settings.controlRoom, `control-${digest(request.event)}`, encrypted);
    store.atomic(() => {
      store.controlDelivered(request.event);
      delegation?.controlDelivered(request.event, success);
    });
    if (process.env.TELEGRAM_CONTEXT_ENABLED === 'true') {
      try {
        const credential = (await readFile('/context-token', 'utf8')).trim();
        const response = await fetch('http://context:8000/events', {
          method: 'POST', redirect: 'error', signal: AbortSignal.timeout(3000),
          headers: { authorization: 'Bearer ' + credential, 'content-type': 'application/json' },
          body: JSON.stringify({ event: 'telegram:control_result', workspace: 'telegram',
            idempotency_key: 'telegram-control-' + digest(request.event),
            data: { session_id: 'telegram-' + digest(request.thread_root ?? request.event),
              timestamp: new Date().toISOString(), action: 'update delivered' } }),
        });
        if (!response.ok) throw new Error('Context Intelligence write failed');
      } catch (error) {
        console.warn('Telegram metadata write failed:', error.name);
      }
    }
  }

  async function notify(draft) {
    const awaiting = draft.state === 'awaiting_approval';
    const title = `Draft for ${draft.binding} · ${draft.id.slice(0, 8)}`;
    const reference = draft.control_event
      ? [`To: ${draft.recipient}`, `Request: ${matrixLink(settings.controlRoom, draft.control_event)}`,
        ...(draft.reply_event ? [`Reply to: ${matrixLink(draft.room, draft.reply_event)}`] : [])]
      : [`Source: https://matrix.to/#/${draft.room}/${draft.source}`];
    const content = awaiting ? [
      title, ...reference, '', draft.body, '',
      `Expires: ${date.format(draft.until)}`, `telegram approve ${draft.id} ${draft.version}`,
      `telegram reject ${draft.id} ${draft.version}`,
    ].join('\n') : `${draft.binding}: ${draft.state.replaceAll('_', ' ')}. Draft ${draft.id}.`;
    const relation = draft.control_event && controlThread(store.controlThread(draft.control_event), draft.control_event);
    const prepared = relation
      ? await matrix.encryptEvent(settings.controlRoom, 'm.room.message', { msgtype: 'm.text', body: content, 'm.relates_to': relation })
      : await matrix.encrypted(settings.controlRoom, content);
    if (!(await matrix.verifyControl())) throw new Error('Control room changed.');
    await matrix.put(settings.controlRoom, `notice-${digest([draft.id, draft.state])}`, prepared);
    if (awaiting && store.draft(draft.id).state === 'awaiting_approval') {
      const question = `${title}\nReview the draft above before sending.`;
      const poll = await matrix.encryptEvent(settings.controlRoom, pollStart, {
        ...(relation && { 'm.relates_to': relation }),
        [pollText]: question,
        [pollStart]: {
          kind: 'org.matrix.msc3381.poll.disclosed',
          max_selections: 1,
          question: { [pollText]: question },
          answers: [
            { id: reviewAnswer(draft, 'approve'), [pollText]: 'Approve and send' },
            { id: reviewAnswer(draft, 'reject'), [pollText]: 'Reject' },
          ],
        },
      });
      if (!(await matrix.verifyControl())) throw new Error('Control room changed.');
      const event = await matrix.put(settings.controlRoom, `review-${draft.id}-${draft.version}`, poll);
      store.registerReview(draft.id, draft.version, event);
    }
    store.notified(draft.id, draft.state);
  }

  async function closeReviewedPoll() {
    const review = store.pendingReviewClose();
    if (!review || !(await matrix.verifyControl())) return;
    const prepared = await matrix.encryptEvent(settings.controlRoom, pollEnd, {
      [pollText]: `Draft review closed for ${review.binding}.`,
      [pollEnd]: {},
      'm.relates_to': { rel_type: 'm.reference', event_id: review.event },
    });
    if (!(await matrix.verifyControl())) throw new Error('Control room changed.');
    await matrix.put(settings.controlRoom, `review-close-${digest(review.event)}`, prepared);
    store.closeReview(review.event);
  }

  async function publish() {
    const draft = store.nextSend(Date.now());
    if (!draft || pausedFile() || store.paused() || Date.now() - lastGood > 15_000) return;
    if (draft.control_event &&
        !(await matrix.verifyControl({ requireDot: store.controlRequiresDot(draft.control_event) }))) return store.stale(draft.id);
    const target = JSON.parse(draft.target);
    if (await liveBinding(draft.room, target) !== target.audience || !(await matrix.verifyControl())) {
      store.pause(draft.binding, 'portal_or_control_room_changed');
      return;
    }
    const ciphertext = draft.ciphertext ?? await matrix.encryptEvent(draft.room, 'm.room.message', {
      msgtype: 'm.text', body: draft.body,
      ...(draft.reply_event ? { 'm.relates_to': { 'm.in_reply_to': { event_id: draft.reply_event } } } : {}),
    });
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
      await closeReviewedPoll();
      await publish();
      await publishControlResult(store.nextControl('ready', now));
      delegation?.settleDrafts();
      if (!worker && !pausedFile() && !store.paused()) {
        const work = store.nextWork(Date.now());
        if (work) worker = processWork(work).catch(() => {
          store.finish(work, 'backend_failed');
          store.pause(work.binding, 'backend_turn_failed');
        }).finally(() => { worker = null; });
        else {
          const request = store.nextControl('queued', now);
          if (request) worker = processControl(request).catch(() => {
            store.controlResult(request, JSON.stringify({ body: 'The Telegram request failed. Try again from Telegram Agent.' }));
          }).finally(() => { worker = null; });
          else if (delegation) worker = delegation.tick().catch(() => {
            console.warn('Telegram Agent request will retry.');
          }).finally(() => { worker = null; });
        }
      }
    } catch {
      lastGood = 0;
      console.warn('Telegram sync or delivery check failed. Retrying without sending.');
      await delay(4_000);
    }
  }
  await writeFile(join(directory, 'last-stop'), new Date().toISOString(), { mode: 0o600 });
  if (worker) await worker;
  for (const socket of sockets) await new Promise(resolve => socket.close(resolve));
  await audit.close();
}
