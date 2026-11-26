import { randomBytes } from 'node:crypto';
import { createServer } from 'node:http';
import { chmod, unlink } from 'node:fs/promises';
import { digest } from './policy.mjs';
import { controlThread, matrixLink } from './control.mjs';
import { matchTelegramName, telegramIntent, telegramMention } from '../../agents/connector/telegram-intent.mjs';
import { telegramTaskStatus } from '../../agents/connector/telegram-office.mjs';

const eventId = /^\$[^\s\x00-\x1f]{1,254}$/u;
const peerId = /^(?:user|chat|channel):[1-9][0-9]{0,18}$/u;
const jobAge = 15 * 60_000;
const toolFor = { list: 'list_rooms', lookup: 'resolve_chat', open: 'open_room', unlink: 'unlink_room',
  draft: 'prepare_draft', send: 'prepare_draft', read: 'read_recent' };
const status = { completed: { status: 'completed' }, blocked: { status: 'blocked' } };

export function draftReceipt(draft, review, action) {
  if (!draft) return null;
  if (action === 'draft' && review) return 'completed';
  if (draft.state === 'bridge_confirmed') return 'completed';
  if (['failed', 'cancelled', 'stale'].includes(draft.state)) return 'blocked';
  if (draft.state === 'unknown') return 'unconfirmed';
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
    selected.set(key, { peer: room.peer, room: room.room ?? null, name, type });
  }
  const matches = [...selected.values()];
  if (scope.action === 'list') return matches.slice(0, 31);
  if (scope.action === 'lookup') return matches.slice(0, 11);
  const exact = matches.filter(({ name, peer, room }) => peer === scope.target || room === scope.target ||
    matchTelegramName(name, scope.target));
  return (exact.length ? exact : matches).slice(0, 11);
}

export function createTelegramDelegation({ store, settings, checkRoom, checkOffice, guide, post, postOffice,
  reactOffice, clearOfficeReaction, runAgent, getBinding, now = Date.now }) {
  store.db.exec(`CREATE TABLE IF NOT EXISTS telegram_task (
    id TEXT PRIMARY KEY, office INTEGER NOT NULL, created INTEGER NOT NULL, fingerprint TEXT NOT NULL,
    thread TEXT, intent TEXT, phase TEXT NOT NULL DEFAULT 'queued', selection TEXT, feedback TEXT,
    outcome TEXT, final_event TEXT
  );`);
  store.db.exec(`CREATE TABLE IF NOT EXISTS telegram_office_receipt (
    id TEXT PRIMARY KEY REFERENCES telegram_task(id), status TEXT, activity TEXT
  );`);
  const grants = new Map();
  const load = id => store.get('SELECT * FROM telegram_task WHERE id=?', id);
  const queued = () => store.get("SELECT * FROM telegram_task WHERE phase IN ('queued','feedback') ORDER BY created LIMIT 1");
  const submit = (id, office, intent) => {
    const fingerprint = digest(intent);
    store.run('INSERT OR IGNORE INTO telegram_task(id,office,created,fingerprint,intent,thread) VALUES (?,?,?,?,?,?)',
      id, Number(office), now(), fingerprint, JSON.stringify(intent), office ? null : id);
    const row = load(id);
    if (row.fingerprint !== fingerprint || Boolean(row.office) !== office) return { status: 'conflict' };
    return receipt(row);
  };
  function receipt(row) {
    if (!row) return { status: 'missing' };
    let state = row.phase === 'done' ? row.outcome : row.phase === 'choosing' ? 'needs_input' : 'pending';
    if (row.phase === 'control') {
      const draft = store.get('SELECT id,state,version FROM draft WHERE control_event=?', row.thread);
      const review = draft && store.get('SELECT event FROM draft_review WHERE draft=? AND version=?', draft.id, draft.version);
      const stopped = !draft && store.get("SELECT 1 FROM control_request WHERE event=? AND status IN ('expired','cancelled')", row.thread);
      state = stopped ? 'blocked' : draftReceipt(draft, review, JSON.parse(row.intent).action) ?? state;
    }
    return { status: state, ...(row.thread && { link: matrixLink(settings.controlRoom, row.thread) }) };
  }
  async function office(input) {
    if (!input || !eventId.test(input.id ?? '') || !settings.officeRoom || input.room !== settings.officeRoom ||
        ![settings.owner, `@dot:${settings.domain}`].includes(input.sender) ||
        !['task-manager', 'manager'].some(key => input.actor === `@${key}:${settings.domain}`)) {
      return { status: 'forbidden' };
    }
    if (input.method === 'status') {
      const row = load(input.id);
      return row?.office ? receipt(row) : { status: 'missing' };
    }
    if (input.method !== 'submit' || !await checkOffice() || !await checkRoom()) return { status: 'unavailable' };
    const intent = telegramIntent(input.text, { office: true });
    return intent ? submit(input.id, true, intent) : { status: 'forbidden' };
  }
  async function directOffice(event) {
    if (event.sender !== settings.owner || event.type !== 'm.room.message' || !eventId.test(event.event_id) ||
        event.content?.['m.relates_to'] || event.content?.['org.matrixconstellation.telegram.request']) return false;
    const text = telegramMention(event.content, settings.agent, 'Telegram Agent');
    if (text === null || !await checkOffice() || !await checkRoom()) return false;
    store.atomic(() => {
      const result = submit(event.event_id, true, telegramIntent(text) ?? { action: 'clarify' });
      if (result.status !== 'conflict') store.run('INSERT OR IGNORE INTO telegram_office_receipt(id) VALUES (?)', event.event_id);
    });
    return true;
  }
  async function publishOffice() {
    const rows = store.all(`SELECT id, status, activity FROM telegram_office_receipt
      WHERE status IS NULL OR status NOT IN ('completed', 'blocked') ORDER BY rowid`);
    for (const row of rows) {
      const result = receipt(load(row.id));
      if (!result.link || result.status === row.status || !Object.hasOwn(telegramTaskStatus, result.status)) continue;
      if (!await checkOffice()) return;
      const state = row.status === null ? 'pending' : result.status;
      const transaction = action => 'telegram-office-' + digest([row.id, action]);
      const activity = row.activity ?? await reactOffice(transaction('activity'), row.id, '👀');
      await postOffice(transaction(state), { msgtype: 'm.text', body: telegramTaskStatus[state] + '\n' + result.link,
        'm.relates_to': controlThread(row.id) });
      if (state === 'completed' || state === 'blocked') {
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
    if (relation?.rel_type === 'm.thread' && content.msgtype === 'm.text' && !content['m.new_content']) {
      const body = typeof content.body === 'string' ? content.body.replace(/^(?:>[^\n]*\n)+\n/u, '').trim() : '';
      const choice = /^telegram choose ([1-9]|10)$/u.exec(body);
      if (!choice) return false;
      if (!await checkRoom()) return true;
      const row = store.get("SELECT * FROM telegram_task WHERE thread=? AND phase='choosing'", relation.event_id);
      const match = row && row.created + jobAge >= now() && JSON.parse(row.selection)[Number(choice[1]) - 1];
      if (match) store.run("UPDATE telegram_task SET phase='queued',selection=? WHERE id=? AND phase='choosing'",
        JSON.stringify(match), row.id);
      return true;
    }
    if (relation || content?.['org.matrixconstellation.telegram.request']) return false;
    const text = telegramMention(content, settings.agent, 'Telegram Agent');
    if (text === null) return false;
    const intent = telegramIntent(text);
    // Existing free-form updates and threaded follow-ups still use the original dispatcher when enabled.
    if (!intent && (settings.bridgeControl || settings.dot)) return false;
    if (await checkRoom()) submit(event.event_id, false, intent ?? { action: 'clarify' });
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
      ...(['draft', 'send'].includes(scope.action) && { allowGivenName: true }),
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
    const { matches, error } = await resolve(row, scope);
    if (error) return feedback(row, error, 'blocked');
    if (!matches.length) return feedback(row, 'No eligible chat matched. Use its exact Telegram username or ID, or verify its binding for drafts and updates.', 'blocked');
    if (matches.length > 10) return feedback(row, 'More than ten chats matched. Start a request with a more specific name or exact Telegram ID.', 'blocked');
    const choices = matches.map((item, index) => `${index + 1}. ${item.name} (${item.type}, ${item.peer})`);
    if (scope.action === 'lookup') return feedback(row, choices.join('\n'), 'completed');
    if (matches.length > 1) {
      return feedback(row, choices.join('\n') + '\n\nReply in this thread with telegram choose <number>.', 'needs_input', matches);
    }
    const destination = matches[0];
    if (tool === 'open_room' || tool === 'unlink_room') {
      if (!await checkRoom()) return;
      // Persist the exact selection before the idempotent bridge operation. A retry cannot resolve another chat.
      store.run('UPDATE telegram_task SET selection=? WHERE id=? AND phase=\'queued\'', JSON.stringify(destination), row.id);
      const remove = tool === 'unlink_room';
      const result = await guide({ id: row.id, action: remove ? 'remove' : 'add',
        target: remove ? destination.room : destination.peer, ...(remove && { retryByRoom: true }) });
      return feedback(row, result?.message ?? 'Telegram Bridge is unavailable.', result?.ok ? 'completed' : 'blocked');
    }
    const binding = destination.room && await getBinding(destination.room, destination.peer);
    if (!binding) return feedback(row, 'This chat needs an active, verified binding. Follow the binding steps and then retry.\n' +
      'https://github.com/Brotherhood94/always-on-constellation-matrix/blob/main/docs/telegram.md#bind-verified-chats-to-the-telegram-space', 'blocked');
    if (!await checkRoom()) return;
    store.atomic(() => {
      const input = { action: tool === 'read_recent' ? 'update' : 'compose', destination: binding.id,
        verifiedBinding: { id: binding.id, room: binding.room, generation: binding.generation, revision: binding.revision },
        chatType: destination.type, ...(tool === 'read_recent' ? { question: scope.question } : { text: scope.text }) };
      if (!store.queueControl(row.thread, input, now(), { requireDot: false, atomic: false })) {
        return feedback(row, 'The Telegram request could not be queued. Try again later.', 'blocked');
      }
      store.run("UPDATE telegram_task SET phase='control',selection=NULL,intent=? WHERE id=?",
        JSON.stringify({ action: scope.action }), row.id);
    });
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
    if (input.tool !== toolFor[scope.action] || grant.running) return { status: 'forbidden' };
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
    if (!row.thread) {
      row.thread = await post('telegram-task-' + digest(row.id), { msgtype: 'm.text', body: 'Telegram request from Office.' });
      store.run('UPDATE telegram_task SET thread=? WHERE id=?', row.thread, row.id);
    }
    if (row.phase === 'feedback') {
      const id = await post('telegram-task-result-' + digest([row.id, row.outcome]), {
        msgtype: 'm.text', body: row.feedback, 'm.relates_to': controlThread(row.thread, row.thread),
      });
      store.run('UPDATE telegram_task SET phase=?,feedback=NULL,final_event=?,intent=? WHERE id=?',
        row.outcome === 'needs_input' ? 'choosing' : 'done', id, row.outcome === 'needs_input' ? row.intent : null, row.id);
      if (row.outcome !== 'needs_input') store.run('UPDATE telegram_task SET selection=NULL WHERE id=?', row.id);
      return;
    }
    const scope = JSON.parse(row.intent);
    if (scope.action === 'clarify') return feedback(row, 'Send one request at a time. For example, say “Send a private message to Sample Contact saying ‘Hello’.” I’ll show a preview and ask for your approval here. You can also ask to find a chat, open or unlink a room, or read an update.', 'blocked');
    const grant = randomBytes(32).toString('hex');
    grants.set(grant, { id: row.id, until: Math.min(row.created + jobAge, now() + 120_000) });
    try {
      await runAgent(grant, toolFor[scope.action]);
    } catch {
      // The persisted operation is authoritative even if ACP fails after its tool call.
    } finally { grants.delete(grant); }
    if (load(row.id).phase === 'queued') feedback(row, 'Telegram Agent could not complete the tool call. Check the Telegram agent service and retry.', 'blocked');
  }
  function controlDelivered(event, success) {
    store.run("UPDATE telegram_task SET phase='done',outcome=?,intent=NULL WHERE thread=? AND phase='control'",
      success ? 'completed' : 'blocked', event);
  }
  function settleDrafts() {
    const rows = store.all("SELECT id FROM telegram_task WHERE phase='control'");
    for (const { id } of rows) {
      const state = receipt(load(id)).status;
      if (['completed', 'blocked'].includes(state)) {
        store.run("UPDATE telegram_task SET phase='done',outcome=?,intent=NULL WHERE id=?", state, id);
      }
    }
  }
  return { office, direct, directOffice, publishOffice, tool, tick, controlDelivered, settleDrafts };
}
