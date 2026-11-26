import { randomBytes } from 'node:crypto';
import { createServer } from 'node:http';
import { chmod, unlink } from 'node:fs/promises';
import { digest } from './policy.mjs';
import { controlThread, matrixLink, operatorNotice } from './control.mjs';
import { matchTelegramName, telegramIntent, telegramMention, telegramTaskPayload } from '../../agents/connector/telegram-intent.mjs';
import { telegramHandoffKey, telegramReceiptKey, telegramTaskStatus, telegramTerminal } from '../../agents/connector/telegram-office.mjs';

const eventId = /^\$[^\s\x00-\x1f]{1,254}$/u;
const peerId = /^(?:user|chat|channel):[1-9][0-9]{0,18}$/u;
const jobAge = 15 * 60_000;
const toolFor = { list: 'list_rooms', lookup: 'resolve_chat', open: 'open_room', unlink: 'unlink_room',
  draft: 'prepare_draft', send: 'send_message', read: 'read_recent', create_group: 'create_group', add_members: 'add_members' };
const status = { completed: { status: 'completed' }, blocked: { status: 'blocked' } };

export function draftReceipt(draft, review) {
  if (!draft) return null;
  if (draft.state === 'cancelled' && review?.decision === 'reject') return 'declined';
  if (draft.state === 'bridge_confirmed') return 'completed';
  if (['failed', 'cancelled', 'stale'].includes(draft.state)) return 'blocked';
  if (draft.state === 'unknown') return 'unconfirmed';
  if (draft.direct_send && ['ready','publishing','matrix_accepted'].includes(draft.state)) return 'sending';
  if (!review) return null;
  return draft.state === 'awaiting_approval' ? 'awaiting_approval' : 'sending';
}

export async function listenTelegramSocket(path, handler) {
  await unlink(path).catch(error => { if (error.code !== 'ENOENT') throw error; });
  const server = createServer(async (req, res) => {
    let result;
    try {
      if (req.method !== 'POST' || req.url !== '/') throw new Error('Invalid route');
      const chunks = [];
      let length = 0;
      for await (const chunk of req) {
        length += chunk.length;
        if (length > 8192) throw new Error('Invalid length');
        chunks.push(chunk);
      }
      result = await handler(JSON.parse(Buffer.concat(chunks).toString()));
    } catch (error) {
      console.warn('Telegram socket request failed:', error.code || error.name);
      result = { status: 'unavailable' };
    }
    res.writeHead(200, { 'content-type': 'application/json' }).end(JSON.stringify(result));
  });
  server.requestTimeout = 7000;
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(path, resolve);
  });
  await chmod(path, 0o600);
  return server;
}

function candidates(rooms, scope) {
  const selected = new Map();
  for (const room of rooms) {
    if (!peerId.test(room.peer ?? '')) continue;
    const type = room.peer.startsWith('user:') ? 'direct' : 'group';
    if (scope.type && scope.type !== type) continue;
    const key = room.room || room.peer;
    const samePeer = [...selected.values()].filter(item => item.peer === room.peer);
    if (!room.room && samePeer.length) continue;
    if (room.room) selected.delete(room.peer);
    const name = String(room.name || room.peer).replace(/[\p{Cc}\p{Cf}]/gu, ' ').slice(0, 120);
    selected.set(key, { peer: room.peer, room: room.room ?? null, name, type, ...(room.needsChoice && { needsChoice: true }) });
  }
  const matches = [...selected.values()];
  if (scope.action === 'list') return matches.slice(0, 31);
  if (scope.action === 'lookup') return matches.slice(0, 11);
  const exact = matches.filter(({ name, peer, room }) => peer === scope.target || room === scope.target ||
    matchTelegramName(name, scope.target));
  return (exact.length ? exact : matches).slice(0, 11);
}

export function createTelegramDelegation({ store, settings, checkRoom, checkOffice, guide, post, postOffice,
  reactOffice, clearOfficeReaction, runAgent, getBinding, prepareBinding, getOfficeEvent, recordOffice, now = Date.now }) {
  store.db.exec(`CREATE TABLE IF NOT EXISTS telegram_task (
    id TEXT PRIMARY KEY, office INTEGER NOT NULL, created INTEGER NOT NULL, fingerprint TEXT NOT NULL,
    thread TEXT, intent TEXT, phase TEXT NOT NULL DEFAULT 'queued', selection TEXT, feedback TEXT,
    outcome TEXT, final_event TEXT
  );`);
  store.db.exec(`CREATE TABLE IF NOT EXISTS telegram_office_receipt (
    id TEXT PRIMARY KEY REFERENCES telegram_task(id), status TEXT, activity TEXT
  );`);
  if (!store.all('PRAGMA table_info(telegram_task)').some(({ name }) => name === 'control_event')) {
    store.db.exec('ALTER TABLE telegram_task ADD COLUMN control_event TEXT');
  }
  for (const column of ['origin', 'handoff']) {
    if (!store.all('PRAGMA table_info(telegram_task)').some(row => row.name === column)) {
      store.db.exec(`ALTER TABLE telegram_task ADD COLUMN ${column} TEXT`);
    }
  }
  store.db.exec('CREATE TABLE IF NOT EXISTS telegram_remote_action (id TEXT PRIMARY KEY, result TEXT)');
  const officeFlow = Boolean(settings.officeFirst);
  const dot = settings.dot ?? `@dot:${settings.domain}`;
  const coordinator = id => ['task-manager', 'manager'].some(key => id === `@${key}:${settings.domain}`);
  const grants = new Map();
  const load = id => store.get('SELECT * FROM telegram_task WHERE id=?', id);
  const queued = () => store.get("SELECT * FROM telegram_task WHERE phase IN ('queued','feedback') ORDER BY created LIMIT 1");
  const submit = (id, office, intent, origin = null, handoff = null) => {
    const fingerprint = digest(intent);
    store.run('INSERT OR IGNORE INTO telegram_task(id,office,created,fingerprint,intent,thread,origin,handoff) VALUES (?,?,?,?,?,?,?,?)',
      id, Number(office), now(), fingerprint, JSON.stringify(intent), office ? null : id, origin, handoff);
    const row = load(id);
    if (row.fingerprint !== fingerprint || Boolean(row.office) !== office) return { status: 'conflict' };
    return receipt(row);
  };
  function outcome(row) {
    let state = row.phase === 'done' ? row.outcome : row.phase === 'choosing' ? 'needs_input' : 'pending';
    let event = row.control_event && row.phase !== 'choosing' ? row.final_event ?? row.thread : row.thread ?? row.final_event;
    if (row.phase === 'control') {
      const control = row.control_event ?? row.thread;
      const draft = store.get('SELECT id,state,version,direct_send FROM draft WHERE control_event=?', control);
      const review = draft && store.get('SELECT event,decision FROM draft_review WHERE draft=? AND version=?', draft.id, draft.version);
      const stopped = !draft && store.get("SELECT 1 FROM control_request WHERE event=? AND status IN ('expired','cancelled')", control);
      state = stopped ? 'blocked' : draftReceipt(draft, review) ?? state;
      event = review?.event ?? event;
    }
    return { state, event };
  }
  function receipt(row) {
    if (!row) return { status: 'missing' };
    const { state, event } = outcome(row);
    if (officeFlow && row.office && row.origin) return { status: state, office: true,
      ...(state === 'unconfirmed' && row.phase === 'done' && { terminal: true }) };
    return { status: state, ...(event && { link: matrixLink(settings.controlRoom, event) }) };
  }
  const standaloneApproval = event => Boolean(event && store.get('SELECT 1 FROM telegram_task WHERE office=1 AND control_event=id AND id=?', event));
  async function office(input) {
    if (!input || !eventId.test(input.id ?? '') || !settings.officeRoom || input.room !== settings.officeRoom ||
        ![settings.owner, `@dot:${settings.domain}`].includes(input.sender) ||
        !coordinator(input.actor)) {
      return { status: 'forbidden' };
    }
    if (input.method === 'status') {
      const row = load(input.id);
      return row?.office ? receipt(row) : { status: 'missing' };
    }
    if (input.method !== 'submit' || !await checkOffice() || !await checkRoom()) return { status: 'unavailable' };
    if (officeFlow) {
      const existing = load(input.id);
      return existing?.office ? receipt(existing) : { status: 'pending', office: true };
    }
    if (Object.hasOwn(input, 'telegram')) {
      const telegram = input.sender === `@dot:${settings.domain}` && input.text === undefined && telegramTaskPayload(input.telegram);
      return telegram ? submit(input.id, true, { action: 'send', ...telegram }) : { status: 'forbidden' };
    }
    const intent = telegramIntent(input.text, { office: true });
    return intent ? submit(input.id, true, intent) : { status: 'forbidden' };
  }
  async function receiveOffice(event) {
    if (!officeFlow || event.type !== 'm.room.message') return false;
    if (event.sender === settings.owner && await choose(event, true)) return true;
    const request = event.content?.[telegramHandoffKey]?.request;
    const relation = event.content?.['m.relates_to'];
    if (!coordinator(event.sender) || !eventId.test(request ?? '') || relation?.rel_type !== 'm.thread' ||
        relation.event_id !== request || telegramMention(event.content, settings.agent, 'Telegram Agent') === null ||
        !await checkRoom() || !await checkOffice()) return false;
    if (load(request)?.origin) return true;
    const original = await getOfficeEvent(request);
    if (!original || ![settings.owner, dot].includes(original.sender) ||
        original.content?.['m.relates_to'] || original.content?.['m.mentions']?.user_ids?.length !== 1 ||
        original.content['m.mentions'].user_ids[0] !== event.sender) return false;
    const label = event.sender.startsWith('@task-manager:') ? 'Task Manager' : 'Manager';
    const text = telegramMention(original.content, event.sender, label);
    if (text === null) return false;
    const plan = original.sender === dot && original.content['net.matrix_agents.dot_plan'];
    if (original.sender === dot && !/^[a-z0-9_-]{8,80}$/iu.test(plan?.task_id ?? '')) return false;
    const structured = plan && telegramTaskPayload(plan.telegram);
    const intent = structured ? { action: 'send', ...structured } :
      telegramIntent(plan ? text.split('\n\n').slice(1).join('\n\n') : text, { office: true });
    if (!intent) return false;
    store.atomic(() => {
      const result = submit(request, true, intent, original.sender, event.event_id);
      if (result.status !== 'conflict') store.run('INSERT OR IGNORE INTO telegram_office_receipt(id) VALUES (?)', request);
    });
    return true;
  }
  async function directOffice(event) {
    if (event.sender !== settings.owner || event.type !== 'm.room.message' || !eventId.test(event.event_id) ||
        event.content?.['m.relates_to'] || event.content?.['org.matrixconstellation.telegram.request']) return false;
    const text = telegramMention(event.content, settings.agent, 'Telegram Agent');
    if (text === null || !await checkOffice() || !await checkRoom()) return false;
    store.atomic(() => {
      const result = submit(event.event_id, true, telegramIntent(text) ?? { action: 'clarify' }, officeFlow ? event.sender : null);
      if (result.status !== 'conflict') store.run('INSERT OR IGNORE INTO telegram_office_receipt(id) VALUES (?)', event.event_id);
    });
    return true;
  }
  async function publishOffice() {
    const rows = store.all(`SELECT id, status, activity FROM telegram_office_receipt
      WHERE status IS NULL OR status NOT IN ('completed', 'blocked', 'declined') ORDER BY rowid`);
    for (const row of rows) {
      const task = load(row.id);
      const result = receipt(task);
      const state = result.status;
      const terminal = telegramTerminal(state) || result.terminal === true;
      if (state === row.status || !Object.hasOwn(telegramTaskStatus, state) || (!result.office && !result.link && !terminal)) continue;
      if (!await checkOffice()) return;
      const transaction = action => 'telegram-office-' + digest([row.id, action]);
      const activity = row.activity ?? await reactOffice(transaction('activity'), row.id, '👀');
      const detail = result.office && state !== 'pending' ? task.feedback : null;
      const body = detail || (result.office && state === 'needs_input' ? 'Choose the intended Telegram chat in this thread.' : telegramTaskStatus[state]);
      const sent = await postOffice(transaction(state), { ...(result.office && state !== 'pending'
        ? operatorNotice(task.origin, body) : { msgtype: 'm.text', body: [body, result.link].filter(Boolean).join('\n') }),
        ...(result.office && { [telegramReceiptKey]: { status: state, office: true, ...(result.terminal && { terminal: true }) } }),
        'm.relates_to': controlThread(row.id, task.handoff ?? row.id) });
      if (result.office && terminal) {
        await recordOffice?.(task, state, sent);
        store.run('UPDATE telegram_task SET final_event=?,feedback=NULL WHERE id=?', sent, row.id);
      }
      if (terminal) {
        await reactOffice(transaction('outcome'), row.id, state === 'completed' ? '✅' : '❌');
        await clearOfficeReaction(transaction('clear'), activity);
      }
      store.run('UPDATE telegram_office_receipt SET status=?,activity=? WHERE id=?', state, activity, row.id);
      return;
    }
  }
  async function direct(event) {
    if (event.sender !== settings.owner || event.type !== 'm.room.message' || !eventId.test(event.event_id)) return false;
    const content = event.content;
    const relation = content?.['m.relates_to'];
    if (await choose(event, false)) return true;
    if (relation || content?.['org.matrixconstellation.telegram.request']) return false;
    const text = telegramMention(content, settings.agent, 'Telegram Agent');
    if (text === null) return false;
    const intent = telegramIntent(text);
    // Existing free-form updates and threaded follow-ups still use the original dispatcher when enabled.
    if (!intent && (settings.bridgeControl || settings.dot)) return false;
    if (await checkRoom()) submit(event.event_id, false, intent ?? { action: 'clarify' });
    return true;
  }
  async function choose(event, office) {
    const content = event.content;
    const relation = content?.['m.relates_to'];
    if (relation?.rel_type !== 'm.thread' || content.msgtype !== 'm.text' || content['m.new_content']) return false;
    const body = typeof content.body === 'string' ? content.body.replace(/^(?:>[^\n]*\n)+\n/u, '').trim() : '';
    const choice = /^(?:telegram choose|scegli|choose) ([1-9]|10)$/iu.exec(body);
    if (!choice) return false;
    if (!await checkRoom()) return true;
    const row = office
      ? store.get("SELECT * FROM telegram_task WHERE id=? AND office=1 AND phase='choosing'", relation.event_id)
      : store.get("SELECT * FROM telegram_task WHERE thread=? AND phase='choosing'", relation.event_id);
    const match = row && row.created + jobAge >= now() && JSON.parse(row.selection)[Number(choice[1]) - 1];
    if (match) store.atomic(() => {
      store.run("UPDATE telegram_task SET phase='queued',selection=?,feedback=NULL,outcome=NULL WHERE id=? AND phase='choosing'",
        JSON.stringify(match), row.id);
      store.run("UPDATE telegram_office_receipt SET status='pending' WHERE id=?", row.id);
    });
    return true;
  }
  function feedback(row, body, outcome, selection = null) {
    store.run("UPDATE telegram_task SET phase='feedback',feedback=?,outcome=?,selection=? WHERE id=? AND phase='queued'",
      body, outcome, selection && JSON.stringify(selection), row.id);
  }
  async function resolve(row, scope) {
    const picked = row.selection && JSON.parse(row.selection);
    if (picked) return { matches: [picked] };
    const result = await guide({ id: row.id, action: 'resolve', target: scope.target, type: scope.type,
      ...(['draft', 'send', 'read', 'add_members'].includes(scope.action) && { allowGivenName: true, ...(officeFlow && { bestMatch: true }) }),
      linkedOnly: !['open', 'lookup'].includes(scope.action), forOpening: scope.action === 'open' });
    if (!result?.ok || !Array.isArray(result.rooms)) return { error: result?.message ?? 'Telegram Bridge is unavailable.' };
    return { matches: candidates(result.rooms, scope) };
  }
  async function perform(row, scope, tool) {
    if (tool === 'list_rooms') {
      const result = await guide({ id: row.id, action: 'list' });
      if (!result?.ok) return feedback(row, result?.message ?? 'Telegram Bridge is unavailable.', 'blocked');
      const rooms = candidates(result.rooms, scope);
      const listing = rooms.slice(0, 30).map(x => `• ${x.name} · ${matrixLink(x.room)}`);
      if (rooms.length > 30) listing.push('Showing 30 linked rooms. Open Telegram → Rooms for the full list.');
      return feedback(row, listing.join('\n') || 'There are no linked Telegram rooms.', 'completed');
    }
    if (scope.action === 'create_group') return mutateGroup(row, scope);
    const { matches, error } = await resolve(row, scope);
    if (error) return feedback(row, error, 'blocked');
    if (!matches.length) return feedback(row, 'No eligible Telegram chat matched. Check the exact @username, peer ID, or chat name. For drafts and updates, first open and join the chat under Telegram → People.', 'blocked');
    if (matches.length > 10) return feedback(row, 'More than ten chats matched. Start a request with a more specific name or exact Telegram ID.', 'blocked');
    const choices = matches.map((item, index) => `${index + 1}. ${item.name} (${item.type}, ${item.peer})`);
    if (scope.action === 'lookup') return feedback(row, choices.join('\n'), 'completed');
    if (matches.length > 1 || (matches[0].needsChoice && !row.selection)) {
      return feedback(row, choices.join('\n') + '\n\nReply in this thread with telegram choose <number>.', 'needs_input', matches);
    }
    const destination = matches[0];
    if (scope.action === 'add_members') return mutateGroup(row, scope, destination);
    if (tool === 'open_room' || tool === 'unlink_room') {
      if (!await checkRoom()) return;
      // Persist the exact selection before the idempotent bridge operation. A retry cannot resolve another chat.
      store.run('UPDATE telegram_task SET selection=? WHERE id=? AND phase=\'queued\'', JSON.stringify(destination), row.id);
      const remove = tool === 'unlink_room';
      const result = await guide({ id: row.id, action: remove ? 'remove' : 'add',
        target: remove ? destination.room : destination.peer, ...(remove && { retryByRoom: true }) });
      if (officeFlow && !remove && result?.ok && result.room) {
        const prepared = await getBinding(result.room, destination.peer) ??
          (await prepareBinding({ ...destination, room: result.room }, row.id, true)).binding;
        if (!prepared) return feedback(row, 'The chat was linked. Its access could not be verified yet. Run the request again after checking Telegram Bridge.', 'blocked');
      }
      return feedback(row, result?.message ?? 'Telegram Bridge is unavailable.', result?.ok ? 'completed' : 'blocked');
    }
    let binding = destination.room && await getBinding(destination.room, destination.peer);
    if (!binding && destination.room && (tool === 'prepare_draft' || (officeFlow && ['send_message', 'read_recent'].includes(tool)))) {
      // Pin the exact destination before changing its relay or binding.
      store.run("UPDATE telegram_task SET selection=? WHERE id=? AND phase='queued'", JSON.stringify(destination), row.id);
      const prepared = await prepareBinding(destination, row.id);
      if (!prepared.binding) return feedback(row, prepared.error, 'blocked');
      binding = prepared.binding;
    }
    if (!binding) return feedback(row, 'This chat needs an active, verified binding. Request a draft for a linked chat to set it up, or check an existing binding in the Telegram guide.\n' +
      'https://github.com/Brotherhood94/always-on-constellation-matrix/blob/main/docs/telegram.md#bind-verified-chats-to-the-telegram-space', 'blocked');
    if (!await checkRoom()) return;
    if (officeFlow && scope.action === 'draft') {
      return feedback(row, `Draft for ${destination.name}\n\n${scope.text}`, 'completed');
    }
    store.atomic(() => {
      const input = { action: tool === 'read_recent' ? 'update' : 'compose', destination: binding.id,
        ...(officeFlow && tool === 'send_message' && { explicitSend: true, operator: settings.owner }),
        verifiedBinding: { id: binding.id, room: binding.room, generation: binding.generation, revision: binding.revision },
        chatType: destination.type, ...(tool === 'read_recent' ? { question: scope.question } : { text: scope.text }) };
      const control = row.office && (officeFlow || ['send', 'draft'].includes(scope.action)) ? row.id : row.thread;
      if (!store.queueControl(control, input, now(), { requireDot: false, atomic: false })) {
        return feedback(row, 'The Telegram request could not be queued. Try again later.', 'blocked');
      }
      store.run("UPDATE telegram_task SET phase='control',selection=NULL,control_event=?,intent=NULL WHERE id=?", control, row.id);
    });
  }
  async function mutateGroup(row, scope, destination) {
    const previous = store.get('SELECT result FROM telegram_remote_action WHERE id=?', row.id);
    if (previous) {
      const result = previous.result && JSON.parse(previous.result);
      return feedback(row, result?.message ?? 'Telegram has not confirmed this group change. Check the original group before trying again.',
        result?.ok ? 'completed' : 'unconfirmed');
    }
    const members = [];
    for (const target of scope.members) {
      const result = await guide({ id: row.id, action: 'resolve', target, type: 'direct', linkedOnly: false, forOpening: true });
      const matches = result?.ok ? candidates(result.rooms ?? [], { action: 'open', type: 'direct', target }) : [];
      const exact = matches.filter(match => match.peer === target || matchTelegramName(match.name, target));
      const allowed = target.startsWith('@') ? matches : exact;
      if (allowed.length !== 1) {
        return feedback(row, `Use one exact Telegram username or peer ID for “${target}”.`, 'blocked');
      }
      members.push(allowed[0].peer);
    }
    if (!await checkRoom() || (row.office && !await checkOffice())) return;
    store.run('INSERT INTO telegram_remote_action(id) VALUES (?)', row.id);
    const result = await guide({ id: row.id, action: scope.action === 'create_group' ? 'create-group' : 'add-members',
      target: destination?.room ?? scope.target, peer: destination?.peer, members: [...new Set(members)] }).catch(() => null);
    let message = result?.message ?? 'The bridge did not confirm this group change. Check Telegram before trying again.';
    if (result?.ok && scope.action === 'create_group' && result.room && result.peer &&
        !await getBinding(result.room, result.peer)) {
      const setup = await prepareBinding({ room: result.room, peer: result.peer }, row.id, true);
      if (!setup.binding) message += '\nThe group was created. Its Matrix binding is pending.';
    }
    if (result?.ok || result?.definitive) {
      store.run('UPDATE telegram_remote_action SET result=? WHERE id=?', JSON.stringify({ ...result, message }), row.id);
    }
    return feedback(row, message, result?.ok ? 'completed' : result?.definitive ? 'blocked' : 'unconfirmed');
  }
  async function tool(input) {
    const grant = grants.get(input?.grant);
    if (!grant || grant.until < now()) return { status: 'forbidden' };
    const row = load(grant.id);
    const directOfficeRequest = row && store.get('SELECT 1 FROM telegram_office_receipt WHERE id=?', row.id);
    if (row?.phase !== 'queued' || !await checkRoom() || (directOfficeRequest && !await checkOffice()) ||
        grant.until < now() || grants.get(input.grant) !== grant) {
      return { status: 'forbidden' };
    }
    const scope = JSON.parse(row.intent);
    if (input.tool === 'resolve_chat' && scope.action !== 'lookup' && scope.target) {
      const { matches, error } = await resolve(row, scope);
      return error ? status.blocked : { status: 'resolved', matches: matches.length };
    }
    if (input.tool !== (scope.action === 'send' && !officeFlow ? 'prepare_draft' : toolFor[scope.action]) || grant.running) return { status: 'forbidden' };
    grant.running = true;
    try {
      await perform(row, scope, input.tool);
      return { status: load(row.id).outcome ?? 'queued' };
    } finally { grant.running = false; }
  }
  async function tick() {
    // Expired choices and unresolved requests stop without touching the bridge.
    store.run(`UPDATE telegram_task SET phase='feedback',feedback='This Telegram request expired. Start a new request.',
      outcome='blocked',selection=NULL WHERE phase IN ('queued','choosing') AND created<?`, now() - jobAge);
    const row = queued();
    if (!row || !await checkRoom()) return;
    const scope = JSON.parse(row.intent);
    if (row.phase === 'feedback') {
      if (officeFlow && row.office && row.origin) {
        store.run("UPDATE telegram_task SET phase=?,intent=? WHERE id=?",
          row.outcome === 'needs_input' ? 'choosing' : 'done', row.outcome === 'needs_input' ? row.intent : null, row.id);
        return;
      }
      const onlyOffice = row.office && scope.action === 'clarify';
      const id = onlyOffice ? null : await post('telegram-task-result-' + digest([row.id, row.outcome]), row.thread
        ? { msgtype: 'm.text', body: row.feedback, 'm.relates_to': controlThread(row.thread) }
        : operatorNotice(settings.owner, row.feedback));
      store.run('UPDATE telegram_task SET phase=?,feedback=NULL,thread=COALESCE(thread,?),final_event=?,intent=? WHERE id=?',
        row.outcome === 'needs_input' ? 'choosing' : 'done', id, id, row.outcome === 'needs_input' ? row.intent : null, row.id);
      if (row.outcome !== 'needs_input') store.run('UPDATE telegram_task SET selection=NULL WHERE id=?', row.id);
      return;
    }
    if (scope.action === 'clarify') return feedback(row, 'Send one request at a time. For example, say “Send a private message to Sample Contact saying ‘Hello’.” You can also ask for a draft preview, open or unlink a chat, manage a group, or request an update.', 'blocked');
    if (!row.thread && !officeFlow && !['send', 'draft'].includes(scope.action)) {
      row.thread = await post('telegram-task-' + digest(row.id), { msgtype: 'm.text', body: 'Telegram request from Office.' });
      store.run('UPDATE telegram_task SET thread=? WHERE id=?', row.thread, row.id);
    }
    const grant = randomBytes(32).toString('hex');
    grants.set(grant, { id: row.id, until: Math.min(row.created + jobAge, now() + 120_000) });
    try {
      await runAgent(grant, scope.action === 'send' && !officeFlow ? 'prepare_draft' : toolFor[scope.action]);
    } catch {
      // The persisted operation is authoritative even if ACP fails after its tool call.
    } finally { grants.delete(grant); }
    if (load(row.id).phase === 'queued') feedback(row, 'Telegram Agent could not complete the tool call. Check the Telegram agent service and retry.', 'blocked');
  }
  function controlDelivered(event, success, reply) {
    store.run("UPDATE telegram_task SET phase='done',outcome=?,final_event=?,intent=NULL WHERE COALESCE(control_event,thread)=? AND phase='control'",
      success ? 'completed' : 'blocked', reply, event);
  }
  function officeControlResult(event, success, body) {
    if (!officeFlow) return false;
    return store.atomic(() => {
      const result = store.run("UPDATE telegram_task SET phase='done',outcome=?,feedback=?,intent=NULL WHERE office=1 AND origin IS NOT NULL AND control_event=? AND phase='control'",
        success ? 'completed' : 'blocked', body, event);
      if (!result.changes) return false;
      store.controlDelivered(event);
      return true;
    });
  }
  function settleDrafts() {
    for (const row of store.all("SELECT * FROM telegram_task WHERE phase='control'")) {
      const { state, event } = outcome(row);
      if (telegramTerminal(state)) {
        const draft = store.get('SELECT recipient,direct_send FROM draft WHERE control_event=?', row.control_event);
        const body = draft?.direct_send && state === 'completed' ? `Telegram confirmed delivery to ${draft.recipient}.` : null;
        store.run("UPDATE telegram_task SET phase='done',outcome=?,final_event=?,feedback=?,intent=NULL WHERE id=?", state, event, body, row.id);
      }
    }
  }
  return { office, receiveOffice, direct, directOffice, publishOffice, tool, tick, controlDelivered, officeControlResult, settleDrafts, standaloneApproval };
}
