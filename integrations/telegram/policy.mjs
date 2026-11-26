import { createHash, randomUUID } from 'node:crypto';
import { DatabaseSync } from 'node:sqlite';

export const digest = (value) => createHash('sha256').update(JSON.stringify(value)).digest('hex');
const bindingID = /^[a-z][a-z0-9-]{0,39}$/u;
const eventID = /^\$[^\s\x00-\x1f]{1,254}$/u;
const loginID = /^[1-9][0-9]{0,18}$/u;
const portalID = /^(user|chat|channel):[1-9][0-9]{0,18}$/u;
const roomID = /^![^\s\x00-\x1f]{1,254}$/u;
const modes = new Set(['manual', 'draft']);
const minimum = 60_000;
const day = 24 * 60 * minimum;
const maxAge = 15 * minimum;
const expiry = 30 * minimum;
export const pollStart = 'org.matrix.msc3381.poll.start';
export const pollEnd = 'org.matrix.msc3381.poll.end';
const pollResponse = 'org.matrix.msc3381.poll.response';
const reviewAnswerID = /^[a-f0-9]{64}$/u;

// The encrypted answer must identify the exact draft even if the relation is visible to the server.
export const reviewAnswer = (draft, action) => digest([draft.id, draft.version, draft.hash, action]);

export function validateBinding(binding, controlRoom) {
  const { id, peer, receiver, login, room, mode, trigger, audience } = binding;
  if (!bindingID.test(id) || !portalID.test(peer) || !loginID.test(login) || receiver !== login ||
      !roomID.test(room) || room === controlRoom || !modes.has(mode) || !/^[a-f0-9]{64}$/u.test(audience)) {
    throw new Error('A verified destination, login, room, and audience are required.');
  }
  if (!['request', 'inbound'].includes(trigger) || (!peer.startsWith('user:') && trigger !== 'request')) {
    throw new Error('The pilot requires an explicit operator trigger for groups.');
  }
}

export function controlCommand({ room, sender, type, content, encrypted }, config) {
  if (!encrypted || room !== config.controlRoom || sender !== config.owner) return null;
  if (type === pollResponse || type === 'm.poll.response') {
    const relation = content?.['m.relates_to'];
    const choices = content?.[type]?.answers ?? (type === 'm.poll.response' ? content?.['m.selections'] : undefined);
    if (relation?.rel_type !== 'm.reference' || !eventID.test(relation.event_id) ||
        !Array.isArray(choices) || choices.length !== 1 ||
        typeof choices[0] !== 'string' || !reviewAnswerID.test(choices[0])) return null;
    return { action: 'review', poll: relation.event_id, choice: choices[0] };
  }
  if (content?.msgtype !== 'm.text' ||
      content['m.relates_to'] || typeof content.body !== 'string') return null;
  const request = /^telegram draft ([a-z][a-z0-9-]{0,39}) (\$[^\s\x00-\x1f]{1,254})$/u.exec(content.body);
  if (request) return { action: 'draft', binding: request[1], source: request[2] };
  const pause = /^telegram pause(?: ([a-z][a-z0-9-]{0,39}))?$/u.exec(content.body);
  return pause ? { action: 'pause', binding: pause[1] } : null;
}

export class TelegramStore {
  constructor(path) {
    this.db = new DatabaseSync(path);
    this.db.exec(`
      PRAGMA journal_mode=WAL;
      PRAGMA busy_timeout=5000;
      PRAGMA synchronous=FULL;
      PRAGMA foreign_keys=ON;
      CREATE TABLE IF NOT EXISTS binding (
        id TEXT PRIMARY KEY, room TEXT UNIQUE NOT NULL, target TEXT NOT NULL,
        generation INTEGER NOT NULL, mode TEXT NOT NULL, trigger TEXT NOT NULL,
        active INTEGER NOT NULL, revision INTEGER NOT NULL, context INTEGER NOT NULL,
        activated INTEGER NOT NULL, session TEXT, engine TEXT
      );
      CREATE TABLE IF NOT EXISTS inbound (
        room TEXT NOT NULL, event TEXT NOT NULL, binding TEXT NOT NULL, generation INTEGER NOT NULL,
        at INTEGER NOT NULL, author TEXT NOT NULL, body TEXT, context INTEGER NOT NULL,
        PRIMARY KEY (room, event)
      );
      CREATE TABLE IF NOT EXISTS work (
        source TEXT NOT NULL, binding TEXT NOT NULL, generation INTEGER NOT NULL,
        revision INTEGER NOT NULL, context INTEGER NOT NULL, queued INTEGER NOT NULL,
        state TEXT NOT NULL, PRIMARY KEY (binding, generation, source)
      );
      CREATE TABLE IF NOT EXISTS draft (
        id TEXT PRIMARY KEY, binding TEXT NOT NULL, generation INTEGER NOT NULL, room TEXT NOT NULL,
        target TEXT NOT NULL, revision INTEGER NOT NULL, context INTEGER NOT NULL, source TEXT NOT NULL,
        version INTEGER NOT NULL, body TEXT NOT NULL, hash TEXT NOT NULL, until INTEGER NOT NULL,
        state TEXT NOT NULL, approval TEXT, txn TEXT UNIQUE NOT NULL, ciphertext TEXT,
        event TEXT UNIQUE, attempted INTEGER, notified TEXT, attempts INTEGER NOT NULL DEFAULT 0, retry_at INTEGER NOT NULL DEFAULT 0
      );
      CREATE TABLE IF NOT EXISTS receipt (
        event TEXT PRIMARY KEY, room TEXT NOT NULL, status TEXT NOT NULL, timing INTEGER NOT NULL
      );
      CREATE TABLE IF NOT EXISTS controls (event TEXT PRIMARY KEY, at INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS control_request (
        event TEXT PRIMARY KEY, requested INTEGER NOT NULL, payload TEXT, status TEXT NOT NULL DEFAULT 'queued',
        result TEXT, binding TEXT, generation INTEGER, revision INTEGER, thread_root TEXT,
        requires_dot INTEGER NOT NULL DEFAULT 1
      );
      CREATE TABLE IF NOT EXISTS draft_review (
        event TEXT PRIMARY KEY, draft TEXT NOT NULL REFERENCES draft(id), version INTEGER NOT NULL,
        hash TEXT NOT NULL, closed INTEGER NOT NULL DEFAULT 0,
        UNIQUE (draft, version)
      );
      CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS work_status ON work(state, queued);
      CREATE INDEX IF NOT EXISTS draft_status ON draft(state, until);
      CREATE INDEX IF NOT EXISTS inbound_recent ON inbound(binding, generation, at);
      CREATE INDEX IF NOT EXISTS control_request_status ON control_request(status, requested);
    `);
    this.atomic(() => {
      if (!this.all('PRAGMA table_info(draft_review)').some(({ name }) => name === 'decision')) {
        this.db.exec('ALTER TABLE draft_review ADD COLUMN decision TEXT');
      }
      const columns = new Set(this.all('PRAGMA table_info(draft)').map(({ name }) => name));
      for (const [name, type] of [
        ['operator_approval', 'INTEGER NOT NULL DEFAULT 0'], ['control_event', 'TEXT'],
        ['recipient', 'TEXT'], ['reply_event', 'TEXT'],
      ]) {
        if (!columns.has(name)) this.db.exec(`ALTER TABLE draft ADD COLUMN ${name} ${type}`);
      }
      const controlColumns = new Set(this.all('PRAGMA table_info(control_request)').map(({ name }) => name));
      if (!controlColumns.has('thread_root')) {
        this.db.exec('ALTER TABLE control_request ADD COLUMN thread_root TEXT');
      }
      if (!controlColumns.has('requires_dot')) {
        this.db.exec('ALTER TABLE control_request ADD COLUMN requires_dot INTEGER NOT NULL DEFAULT 1');
      }
      this.db.exec('CREATE INDEX IF NOT EXISTS control_request_thread ON control_request(thread_root, requested)');
    });
  }

  get(sql, ...values) { return this.db.prepare(sql).get(...values); }
  all(sql, ...values) { return this.db.prepare(sql).all(...values); }
  run(sql, ...values) { return this.db.prepare(sql).run(...values); }
  atomic(work) {
    this.db.exec('BEGIN IMMEDIATE');
    try {
      const result = work();
      this.db.exec('COMMIT');
      return result;
    } catch (error) {
      this.db.exec('ROLLBACK');
      throw error;
    }
  }

  binding(id) { return this.get('SELECT * FROM binding WHERE id=?', id); }
  bindings() { return this.all('SELECT * FROM binding ORDER BY id'); }
  inRoom(room) { return this.get('SELECT * FROM binding WHERE room=?', room); }
  setting(key) { return this.get('SELECT value FROM settings WHERE key=?', key)?.value; }
  set(key, value) { this.run('INSERT OR REPLACE INTO settings VALUES (?, ?)', key, String(value)); }
  paused() { return this.setting('paused') === 'true'; }

  bind(binding, controlRoom, now, { createOnly = false } = {}) {
    validateBinding(binding, controlRoom);
    const target = JSON.stringify({ peer: binding.peer, login: binding.login, receiver: binding.receiver,
      operator: binding.operator, audience: binding.audience });
    return this.atomic(() => {
      const before = this.binding(binding.id);
      if (createOnly && (before || this.inRoom(binding.room) || this.paused())) return false;
      if (before && (before.room !== binding.room || before.target !== target)) {
        throw new Error('A changed destination or audience requires an explicit new binding generation.');
      }
      const conflicting = this.inRoom(binding.room);
      if (conflicting && conflicting.id !== binding.id) throw new Error('This room already has a binding.');
      if (!before) {
        this.run(`INSERT INTO binding VALUES (?, ?, ?, 1, ?, ?, 1, 1, 0, ?, NULL, NULL)`,
          binding.id, binding.room, target, binding.mode, binding.trigger, now);
      } else if (!before.active || before.trigger !== binding.trigger || before.mode !== binding.mode) {
        this.invalidate(binding.id);
        this.run('UPDATE binding SET mode=?, trigger=?, active=1, activated=?, revision=revision+1 WHERE id=?',
          binding.mode, binding.trigger, now, binding.id);
      }
      this.run('DELETE FROM settings WHERE key=?', 'pause_reason:' + binding.id);
      return true;
    });
  }

  invalidate(id, state = 'stale') {
    this.run('UPDATE work SET state=? WHERE binding=? AND state=?', state, id, 'queued');
    this.run(`UPDATE draft SET state=? WHERE binding=? AND state IN ('awaiting_approval','ready')`, state, id);
  }

  reset(binding, controlRoom, now) {
    validateBinding(binding, controlRoom);
    const target = JSON.stringify({ peer: binding.peer, login: binding.login, receiver: binding.receiver,
      operator: binding.operator, audience: binding.audience });
    this.atomic(() => {
      const old = this.binding(binding.id);
      if (!old || old.active || this.get(`SELECT 1 FROM draft WHERE binding=? AND state IN ('publishing','matrix_accepted','unknown')`, binding.id)) {
        throw new Error('Pause the existing binding and reconcile its in-flight work before rotating it.');
      }
      this.invalidate(binding.id);
      this.run(`UPDATE binding SET room=?, target=?, mode=?, trigger=?, generation=generation+1,
        revision=revision+1, context=0, activated=?, session=NULL, engine=NULL WHERE id=?`,
        binding.room, target, binding.mode, binding.trigger, now, binding.id);
      this.set('pause_reason:' + binding.id, 'binding_rotated');
    });
  }

  pause(id, reason = 'operator_paused') {
    this.atomic(() => {
      if (!id) {
        this.set('paused', true);
        this.run("UPDATE control_request SET status='cancelled', payload=NULL, result=NULL WHERE status IN ('queued','ready')");
      }
      for (const item of id ? this.bindings().filter(row => row.id === id) : this.bindings()) {
        this.invalidate(item.id, 'cancelled');
        const update = this.run('UPDATE binding SET active=0, revision=revision+1 WHERE id=? AND active=1', item.id);
        if (update.changes) this.set('pause_reason:' + item.id, reason);
      }
    });
  }

  recover(now) {
    this.atomic(() => {
      this.run(`UPDATE draft SET state='unknown' WHERE state='publishing'`);
      for (const { binding } of this.all(`SELECT DISTINCT binding FROM work WHERE state='running'`)) {
        this.invalidate(binding);
        this.run('UPDATE binding SET active=0, revision=revision+1 WHERE id=?', binding);
        this.set('pause_reason:' + binding, 'interrupted_turn');
      }
      this.run(`UPDATE work SET state='interrupted' WHERE state='running'`);
      this.expire(now);
    });
  }

  expire(now) {
    this.run(`UPDATE draft SET state='stale' WHERE state IN ('ready','awaiting_approval') AND until<=?`, now);
    this.run(`UPDATE draft SET state='unknown' WHERE state='matrix_accepted' AND attempted<?`, now - 5 * minimum);
    this.run(`UPDATE work SET state='stale' WHERE state='queued' AND queued<?`, now - maxAge);
    this.run("UPDATE control_request SET status='expired', payload=NULL, result=NULL WHERE status IN ('queued','ready') AND requested<?",
      now - maxAge);
  }

  retain(now) {
    this.run(`UPDATE inbound SET body=NULL WHERE body IS NOT NULL AND at<?`, now - 60 * minimum);
    this.run(`UPDATE draft SET body='',ciphertext=NULL WHERE body<>'' AND until<?
      AND state IN ('bridge_confirmed','failed','cancelled','stale')`, now - 24 * 60 * minimum);
    this.run(`DELETE FROM receipt WHERE timing<?`, now - 24 * 60 * minimum);
    this.run("DELETE FROM control_request WHERE requested<? AND status NOT IN ('queued','ready')", now - day);
  }

  queueControl(event, request, now, { requireDot = true, atomic = true } = {}) {
    const insert = () => {
      if (!eventID.test(event) || this.paused() ||
          this.get('SELECT count(*) AS count FROM control_request WHERE requested>?', now - 60 * minimum).count >= 24) return false;
      if (request.thread && !eventID.test(request.thread)) return false;
      return !!this.run('INSERT OR IGNORE INTO control_request(event, requested, payload, thread_root, requires_dot) VALUES (?, ?, ?, ?, ?)',
        event, now, JSON.stringify(request), request.thread ?? event, Number(requireDot)).changes;
    };
    return atomic ? this.atomic(insert) : insert();
  }

  nextControl(status, now) {
    return this.get('SELECT * FROM control_request WHERE status=? AND requested>=? ORDER BY requested LIMIT 1',
      status, now - maxAge);
  }

  controlContext(thread) {
    return this.get(`SELECT binding, generation, revision FROM control_request
      WHERE (thread_root=? OR event=?) AND status IN ('ready','done') AND binding IS NOT NULL
      ORDER BY requested DESC LIMIT 1`, thread, thread);
  }

  controlThread(event) {
    return this.get('SELECT thread_root FROM control_request WHERE event=?', event)?.thread_root ?? event;
  }

  controlRequiresDot(event) {
    return this.get('SELECT requires_dot FROM control_request WHERE event=?', event)?.requires_dot !== 0;
  }

  controlDraft(thread, binding, reply) {
    return this.get(`SELECT draft.*, review.event AS review_event FROM draft
      JOIN control_request request ON request.event=draft.control_event
      LEFT JOIN draft_review review ON review.draft=draft.id AND review.version=draft.version
      WHERE (request.thread_root=? OR request.event=?) AND draft.binding=? AND draft.reply_event=?
        AND draft.state IN ('awaiting_approval','ready','publishing','matrix_accepted','bridge_confirmed','unknown')
      ORDER BY draft.until DESC LIMIT 1`, thread, thread, binding, reply);
  }

  controlResult(request, result, binding) {
    this.run("UPDATE control_request SET status='ready', payload=NULL, result=?, binding=?, generation=?, revision=? WHERE event=? AND status='queued'",
      result, binding?.id ?? null, binding?.generation ?? null, binding?.revision ?? null, request.event);
  }

  controlDelivered(event) {
    this.run("UPDATE control_request SET status='done', payload=NULL, result=NULL WHERE event=?", event);
  }

  prepareControlDraft(request, binding, recipient, text, reply, now, evidence) {
    return this.atomic(() => {
      const verified = evidence?.room === binding.room && evidence.event === reply;
      if (!this.get("SELECT 1 FROM control_request WHERE event=? AND status='queued' AND requested>=?", request.event, now - maxAge) ||
          !this.current({ ...binding, binding: binding.id }, now) || binding.mode === 'manual' ||
          !eventID.test(reply ?? request.event) || typeof text !== 'string' || !text.trim() || Buffer.byteLength(text) > 3500 ||
          (reply && !verified && !this.get(`SELECT 1 FROM inbound WHERE room=? AND event=? AND binding=? AND generation=? AND author='remote'`,
            binding.room, reply, binding.id, binding.generation))) return false;
      this.insertDraft(binding, text, reply ?? request.event, now, { control: request.event, recipient, reply });
      this.run('UPDATE control_request SET binding=?, generation=?, revision=? WHERE event=?',
        binding.id, binding.generation, binding.revision, request.event);
      this.controlDelivered(request.event);
      return true;
    });
  }

  record(incoming, now) {
    if (!eventID.test(incoming.event) || !roomID.test(incoming.room) || !['remote', 'owner'].includes(incoming.author)) return false;
    return this.atomic(() => {
      const binding = this.inRoom(incoming.room);
      if (!binding?.active || this.paused()) return false;
      if (!Number.isSafeInteger(incoming.ts) || incoming.ts < binding.activated || incoming.ts > now + minimum) return false;
      const revision = binding.context + 1;
      const body = typeof incoming.body === 'string' && Buffer.byteLength(incoming.body) <= 4_000 ? incoming.body : null;
      if (!this.run(`INSERT OR IGNORE INTO inbound VALUES (?, ?, ?, ?, ?, ?, ?, ?)`, binding.room,
        incoming.event, binding.id, binding.generation, now, incoming.author, body, revision).changes) return false;
      this.run('UPDATE binding SET context=? WHERE id=?', revision, binding.id);
      this.invalidate(binding.id);
      if (incoming.author === 'remote' && body && incoming.ts >= now - maxAge && binding.trigger === 'inbound') {
        this.enqueue({ ...binding, context: revision }, incoming.event, now);
      }
      return true;
    });
  }

  enqueue(binding, event, now) {
    if (!binding.active || binding.mode === 'manual' || this.paused()) return false;
    const queued = this.get(`SELECT COUNT(*) AS n FROM work WHERE state IN ('queued','running')`).n;
    const hour = this.get(`SELECT COUNT(*) AS n FROM work WHERE queued>?`, now - 60 * minimum).n;
    const chat = this.get(`SELECT COUNT(*) AS n FROM work WHERE binding=? AND queued>?`, binding.id, now - maxAge).n;
    const generationTurns = this.get(`SELECT COUNT(*) AS n FROM work WHERE binding=? AND generation=?`, binding.id, binding.generation).n;
    if (queued >= 32 || hour >= 24 || chat >= 4 || generationTurns >= 32) return false;
    return Boolean(this.run('INSERT OR IGNORE INTO work VALUES (?, ?, ?, ?, ?, ?, ?)',
      event, binding.id, binding.generation, binding.revision, binding.context, now, 'queued').changes);
  }

  command(event, command, config, now) {
    if (!eventID.test(event.event) || event.room !== config.controlRoom || event.sender !== config.owner || !event.encrypted) return false;
    return this.atomic(() => {
      if (!this.run('INSERT OR IGNORE INTO controls VALUES (?, ?)', event.event, now).changes) return false;
      if (command.action === 'pause') {
        if (!command.binding) {
          this.set('paused', true);
          this.run("UPDATE control_request SET status='cancelled', payload=NULL, result=NULL WHERE status IN ('queued','ready')");
        }
        for (const binding of this.bindings()) {
          if (command.binding && command.binding !== binding.id) continue;
          this.invalidate(binding.id, 'cancelled');
          this.run('UPDATE binding SET active=0, revision=revision+1 WHERE id=?', binding.id);
        }
        return true;
      }
      if (command.action === 'draft') {
        const binding = this.binding(command.binding);
        if (!binding) return false;
        const source = this.get('SELECT * FROM inbound WHERE room=? AND event=?', binding.room, command.source);
        if (!source?.body || source.author !== 'remote' || source.generation !== binding.generation ||
            source.context !== binding.context || source.at < now - maxAge) return false;
        return this.enqueue(binding, source.event, now);
      }
      if (command.action !== 'review') return false;
      const review = this.get('SELECT * FROM draft_review WHERE event=? AND closed=0', command.poll);
      if (!review) return false;
      const draft = this.draft(review.draft);
      if (!draft || draft.version !== review.version || review.hash !== draft.hash ||
          draft.state !== 'awaiting_approval' || !this.current(draft, now)) return false;
      const decision = ['approve', 'reject'].find(action => command.choice === reviewAnswer(draft, action));
      if (decision === 'reject') {
        this.run(`UPDATE draft SET state='cancelled' WHERE id=?`, draft.id);
      } else if (decision === 'approve') {
        const approval = JSON.stringify({ event: event.event, sender: event.sender, poll: review.event, hash: draft.hash, version: draft.version,
          target: draft.target, room: draft.room, generation: draft.generation, revision: draft.revision,
          context: draft.context, expiry: draft.until, at: now });
        this.run(`UPDATE draft SET state='ready', approval=? WHERE id=?`, approval, draft.id);
      } else return false;
      this.run('UPDATE draft_review SET decision=? WHERE event=?', decision, review.event);
      return true;
    });
  }

  current(item, now) {
    const binding = this.binding(item.binding);
    return !this.paused() && binding?.active && binding.generation === item.generation && binding.revision === item.revision &&
      binding.context === item.context && (item.until === undefined || item.until > now);
  }

  nextWork(now) {
    return this.atomic(() => {
      const work = this.get(`SELECT * FROM work WHERE state='queued' ORDER BY queued LIMIT 1`);
      if (!work) return;
      const state = this.current(work, now) ? 'running' : 'stale';
      this.finish(work, state);
      if (state === 'running') return work;
    });
  }

  finish(work, state) {
    this.run(`UPDATE work SET state=? WHERE binding=? AND generation=? AND source=?`, state, work.binding, work.generation, work.source);
  }

  session(work, id, engine) {
    if (typeof id !== 'string' || !id || id.length > 320) throw new Error('Invalid ACP session identity.');
    const changed = this.run(`UPDATE binding SET session=?, engine=? WHERE id=? AND generation=? AND revision=?
      AND (session IS NULL OR (session=? AND engine=?))`, id, engine, work.binding, work.generation, work.revision, id, engine);
    if (!changed.changes) throw new Error('The ACP session binding changed.');
  }

  history(work) {
    return this.all(`SELECT event, author, body FROM inbound WHERE binding=? AND generation=? AND body IS NOT NULL
      ORDER BY context DESC LIMIT 8`, work.binding, work.generation).reverse();
  }

  saveCandidate(work, body, now) {
    return this.atomic(() => {
      const binding = this.binding(work.binding);
      if (!this.current(work, now)) return this.finish(work, 'stale');
      if (typeof body !== 'string' || !body.trim() || Buffer.byteLength(body) > 3_500) {
        return this.finish(work, 'invalid_candidate');
      }
      const id = this.insertDraft(binding, body, work.source, now);
      this.finish(work, 'completed');
      return id;
    });
  }

  insertDraft(binding, body, source, now, { control, recipient, reply } = {}) {
    const id = randomUUID();
    const state = 'awaiting_approval';
    this.run(`INSERT INTO draft (id,binding,generation,room,target,revision,context,source,version,body,hash,until,state,txn,
      operator_approval,control_event,recipient,reply_event) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    id, binding.id, binding.generation, binding.room, binding.target, binding.revision, binding.context, source,
    body, digest(body), now + expiry, state, `telegram-${id}`, Number(!!control), control ?? null, recipient ?? null, reply ?? null);
    return id;
  }

  draft(id) { return this.get('SELECT * FROM draft WHERE id=?', id); }
  registerReview(draftId, version, event) {
    const draft = this.draft(draftId);
    if (!draft || draft.version !== version || !eventID.test(event)) return;
    this.run('INSERT OR IGNORE INTO draft_review (event, draft, version, hash) VALUES (?, ?, ?, ?)',
      event, draft.id, draft.version, draft.hash);
  }

  migrateReviews(room, legacyRoom, anchors, now) {
    return this.atomic(() => {
      if (this.setting('review_room') === room) return;
      if (this.get("SELECT 1 FROM draft WHERE state IN ('publishing','matrix_accepted','unknown')")) {
        throw new Error('Reconcile Telegram delivery receipts before changing the review room.');
      }
      this.run("UPDATE binding SET mode='draft' WHERE mode='auto'");
      this.expire(now);
      if (legacyRoom && legacyRoom !== room) {
        this.run("UPDATE draft_review SET closed=1");
        this.run("UPDATE control_request SET status='cancelled', payload=NULL, result=NULL WHERE status IN ('queued','ready')");
      }
      for (const draft of this.all("SELECT * FROM draft WHERE state IN ('ready','awaiting_approval')")) {
        if (!this.current(draft, now)) {
          this.run("UPDATE draft SET state='stale', notified='stale' WHERE id=?", draft.id);
          continue;
        }
        const event = legacyRoom ? anchors.get(draft.control_event) : draft.control_event;
        if (legacyRoom && event) {
          const request = this.get('SELECT * FROM control_request WHERE event=?', draft.control_event);
          this.run(`INSERT OR IGNORE INTO control_request(event, requested, status, binding, generation, revision,
            thread_root, requires_dot) VALUES (?, ?, 'done', ?, ?, ?, ?, 0)`, event, request?.requested ?? now,
          draft.binding, draft.generation, draft.revision, event);
          if (this.get("SELECT 1 FROM sqlite_master WHERE type='table' AND name='telegram_task'")) {
            this.run('UPDATE telegram_task SET thread=? WHERE thread=?', event, draft.control_event);
          }
        }
        this.run(`UPDATE draft SET state='awaiting_approval', approval=NULL, notified=NULL, version=version+1,
          control_event=?, ciphertext=NULL, retry_at=0 WHERE id=?`, event ?? null, draft.id);
      }
      this.set('review_room', room);
    });
  }

  pendingReviewClose() {
    return this.get(`SELECT review.event, draft.binding FROM draft_review review JOIN draft ON draft.id=review.draft
      WHERE review.closed=0 AND (draft.state!='awaiting_approval' OR review.version!=draft.version)
      ORDER BY draft.until LIMIT 1`);
  }

  closeReview(event) { this.run('UPDATE draft_review SET closed=1 WHERE event=?', event); }

  nextSend(now) {
    return this.get(`SELECT candidate.* FROM draft candidate WHERE candidate.state='ready' AND candidate.retry_at<=?
      AND NOT EXISTS (SELECT 1 FROM draft active WHERE active.binding=candidate.binding
      AND active.state IN ('publishing','matrix_accepted','unknown')) ORDER BY candidate.until LIMIT 1`, now);
  }
  stale(id) { this.run(`UPDATE draft SET state='stale' WHERE id=? AND state='ready'`, id); }

  prepareSend(id, ciphertext, now) {
    return this.atomic(() => {
      const draft = this.draft(id);
      if (!draft || draft.state !== 'ready' || !this.current(draft, now)) return false;
      const binding = this.binding(draft.binding);
      if (binding.target !== draft.target || binding.room !== draft.room || digest(draft.body) !== draft.hash) return false;
      const approval = JSON.parse(draft.approval ?? '{}');
      const review = this.get('SELECT event FROM draft_review WHERE draft=? AND version=? AND hash=?',
        draft.id, draft.version, draft.hash);
      if (!review || approval.poll !== review.event ||
        approval.sender !== JSON.parse(binding.target).operator || approval.hash !== draft.hash ||
        approval.version !== draft.version || approval.room !== draft.room ||
        approval.target !== draft.target || approval.generation !== draft.generation || approval.revision !== draft.revision ||
        approval.context !== draft.context || approval.expiry !== draft.until) return false;
      if (draft.ciphertext && draft.ciphertext !== ciphertext) throw new Error('Refusing to replace prepared ciphertext.');
      this.run(`UPDATE draft SET state='publishing', attempted=?, ciphertext=?, attempts=attempts+1 WHERE id=?`, now, ciphertext, id);
      return true;
    });
  }

  accepted(txn, room, event) {
    if (!eventID.test(event)) throw new Error('Invalid Matrix event identity.');
    this.atomic(() => {
      const draft = this.get('SELECT id,event,room,state FROM draft WHERE txn=?', txn);
      if (!draft || draft.room !== room || (draft.event && draft.event !== event)) return;
      if (!['publishing', 'unknown', 'matrix_accepted'].includes(draft.state)) return;
      const receipt = this.get('SELECT status FROM receipt WHERE room=? AND event=?', room, event);
      this.run('UPDATE draft SET state=?,event=? WHERE id=?', receipt?.status ?? 'matrix_accepted', event, draft.id);
    });
  }

  retry(id, now) {
    this.run(`UPDATE draft SET state=CASE WHEN attempts<3 AND until>? THEN 'ready' ELSE 'failed' END,
      retry_at=? WHERE id=? AND state='publishing'`, now + 30_000, now + 30_000, id);
  }

  sendError(id) {
    this.run(`UPDATE draft SET state='unknown' WHERE id=? AND state='publishing'`, id);
  }

  delivery(room, event, status, now) {
    if (!eventID.test(event) || !this.inRoom(room) || !['SUCCESS', 'FAIL_PERMANENT'].includes(status)) return;
    this.atomic(() => {
      const state = status === 'SUCCESS' ? 'bridge_confirmed' : 'failed';
      this.run(`INSERT OR IGNORE INTO receipt VALUES (?, ?, ?, ?)`, event, room, state, now);
      const evidence = this.get('SELECT status FROM receipt WHERE room=? AND event=?', room, event);
      if (evidence) this.run(`UPDATE draft SET state=? WHERE room=? AND event=? AND state IN ('matrix_accepted','unknown')`,
        evidence.status, room, event);
    });
  }

  pendingNotice() {
    return this.get(`SELECT * FROM draft WHERE state IN ('awaiting_approval','bridge_confirmed','failed','unknown','stale','cancelled')
      AND COALESCE(notified, '') != state ORDER BY until LIMIT 1`);
  }

  notified(id, state) { this.run('UPDATE draft SET notified=? WHERE id=?', state, id); }
}
