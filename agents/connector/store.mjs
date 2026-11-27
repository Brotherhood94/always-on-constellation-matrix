import { DatabaseSync } from 'node:sqlite';

export class Store {
  constructor(path) {
    this.db = new DatabaseSync(path);
    this.db.exec(`
      PRAGMA journal_mode=WAL;
      PRAGMA synchronous=FULL;
      CREATE TABLE IF NOT EXISTS threads (
        root TEXT PRIMARY KEY, session_id TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS inbox (
        id TEXT PRIMARY KEY, thread TEXT NOT NULL, body TEXT NOT NULL,
        reply TEXT, sent TEXT, request TEXT, number INTEGER NOT NULL DEFAULT 1, delegator TEXT, reply_to TEXT
      );
      CREATE TABLE IF NOT EXISTS settings (
        name TEXT PRIMARY KEY, value TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS assignment_reactions (
        assignment TEXT PRIMARY KEY, started INTEGER NOT NULL DEFAULT 0,
        activity_id TEXT, activity_removed INTEGER NOT NULL DEFAULT 0,
        outcome_key TEXT, outcome_id TEXT
      );
      CREATE TABLE IF NOT EXISTS outgoing_attachments (
        assignment TEXT NOT NULL, path TEXT NOT NULL, draft TEXT NOT NULL, sent TEXT,
        PRIMARY KEY (assignment, path)
      );
      CREATE TABLE IF NOT EXISTS context_outbox (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS staffing_receipts (
        id TEXT PRIMARY KEY, checked INTEGER NOT NULL DEFAULT 0, done INTEGER NOT NULL DEFAULT 0
      );
      CREATE TABLE IF NOT EXISTS staffing_closed (request TEXT PRIMARY KEY);
      CREATE TABLE IF NOT EXISTS office_telegram (
        id TEXT PRIMARY KEY, thread TEXT NOT NULL, status TEXT, checked INTEGER NOT NULL DEFAULT 0
      );
    `);
    const columns = this.db.prepare('PRAGMA table_info(inbox)').all().map(column => column.name);
    if (!columns.includes('request')) this.db.exec('ALTER TABLE inbox ADD COLUMN request TEXT');
    if (!columns.includes('number')) this.db.exec('ALTER TABLE inbox ADD COLUMN number INTEGER NOT NULL DEFAULT 1');
    if (!columns.includes('delegator')) this.db.exec('ALTER TABLE inbox ADD COLUMN delegator TEXT');
    if (!columns.includes('reply_to')) this.db.exec('ALTER TABLE inbox ADD COLUMN reply_to TEXT');
    if (!columns.includes('attachment_refs')) this.db.exec('ALTER TABLE inbox ADD COLUMN attachment_refs TEXT');
    if (!columns.includes('managed')) this.db.exec('ALTER TABLE inbox ADD COLUMN managed TEXT');
    if (!columns.includes('received')) this.db.exec('ALTER TABLE inbox ADD COLUMN received INTEGER');
    if (!columns.includes('outgoing')) this.db.exec('ALTER TABLE inbox ADD COLUMN outgoing TEXT');
    this.db.exec('CREATE UNIQUE INDEX IF NOT EXISTS inbox_request_number ON inbox(request, number)');
  }

  activatedAt() {
    this.db.prepare("INSERT OR IGNORE INTO settings VALUES ('activatedAt', ?)").run(String(Date.now()));
    return Number(this.db.prepare("SELECT value FROM settings WHERE name='activatedAt'").get().value);
  }

  enqueue({ id, thread, body, request = id, number = 1, delegator = null, replyTo = null, attachmentRefs, managed, received = null }) {
    this.db.prepare(`INSERT OR IGNORE INTO inbox (id, thread, body, request, number, delegator, reply_to, attachment_refs, managed, received)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`)
      .run(id, thread, body, request, number, delegator, replyTo,
        attachmentRefs ? JSON.stringify(attachmentRefs) : null, managed ? JSON.stringify(managed) : null, received);
  }

  next() {
    const row = this.db.prepare(`SELECT id, thread, body, reply, COALESCE(request, id) AS request, number,
                                        delegator, reply_to AS replyTo, attachment_refs AS attachmentRefs, managed, outgoing, received
                                 FROM inbox WHERE sent IS NULL ORDER BY rowid LIMIT 1`).get();
    if (row) {
      row.attachmentRefs = row.attachmentRefs ? JSON.parse(row.attachmentRefs) : [];
      row.managed = row.managed ? JSON.parse(row.managed) : null;
      row.outgoing = row.outgoing ? JSON.parse(row.outgoing) : null;
    }
    return row;
  }

  session(thread) {
    return this.db.prepare('SELECT session_id FROM threads WHERE root=?').get(thread)?.session_id;
  }

  saveSession(thread, id) {
    this.db.prepare('INSERT OR REPLACE INTO threads VALUES (?, ?)').run(thread, id);
  }

  saveReply(event, reply) {
    this.db.prepare("UPDATE inbox SET reply=?, body='', attachment_refs=NULL WHERE id=?").run(reply, event);
  }

  saveOutgoing(event, outgoing) {
    this.db.prepare('UPDATE inbox SET outgoing=? WHERE id=?').run(JSON.stringify(outgoing), event);
  }

  ignoreAssignment(id) {
    this.db.prepare("UPDATE inbox SET sent='', body='', attachment_refs=NULL WHERE id=?").run(id);
  }

  attachment(event, path) {
    const row = this.db.prepare('SELECT draft, sent FROM outgoing_attachments WHERE assignment=? AND path=?').get(event, path);
    if (row) return { ...JSON.parse(row.draft), sent: row.sent };
  }

  saveAttachment(event, path, draft) {
    this.db.prepare('INSERT INTO outgoing_attachments (assignment, path, draft) VALUES (?, ?, ?)')
      .run(event, path, JSON.stringify(draft));
  }

  attachmentSent(event, path, id) {
    this.db.prepare('UPDATE outgoing_attachments SET sent=? WHERE assignment=? AND path=?').run(id, event, path);
  }

  startActivity(event) {
    this.db.prepare(`INSERT INTO assignment_reactions (assignment, started) VALUES (?, 1)
                     ON CONFLICT (assignment) DO UPDATE SET started=1`).run(event);
  }

  activityPending(event) {
    return Boolean(this.db.prepare(`SELECT 1 FROM assignment_reactions
                                    WHERE assignment=? AND started=1 AND activity_id IS NULL`).get(event));
  }

  activitySent(event, id) {
    this.db.prepare('UPDATE assignment_reactions SET activity_id=? WHERE assignment=?').run(id, event);
  }

  acceptEvidence(event, evidence) {
    this.db.exec('BEGIN IMMEDIATE');
    try {
      this.startActivity(event);
      if (evidence) this.db.prepare('INSERT OR IGNORE INTO context_outbox VALUES (?, ?)').run(
        'accept:' + event, JSON.stringify(evidence));
      this.db.exec('COMMIT');
    } catch (error) {
      this.db.exec('ROLLBACK');
      throw error;
    }
  }

  sent(event, id, outcomeKey, evidence, staffingReceipt) {
    this.db.exec('BEGIN IMMEDIATE');
    try {
      if (staffingReceipt) {
        // Preserve the original dispatch event as the hiring handoff anchor.
        this.db.prepare('INSERT INTO staffing_closed VALUES (?)').run(event);
        this.db.prepare('UPDATE staffing_receipts SET done=1 WHERE id=?').run(staffingReceipt);
      } else {
        this.db.prepare('UPDATE inbox SET sent=?, reply=NULL, outgoing=NULL WHERE id=?').run(id, event);
      }
      this.db.prepare('DELETE FROM outgoing_attachments WHERE assignment=?').run(event);
      if (evidence) {
        this.db.prepare('INSERT OR IGNORE INTO context_outbox VALUES (?, ?)').run(id, JSON.stringify(evidence));
      }
      if (outcomeKey) {
        this.db.prepare(`INSERT INTO assignment_reactions (assignment, outcome_key) VALUES (?, ?)
                         ON CONFLICT (assignment) DO UPDATE SET outcome_key=COALESCE(outcome_key, excluded.outcome_key)`)
          .run(event, outcomeKey);
      }
      this.db.exec('COMMIT');
    } catch (error) {
      this.db.exec('ROLLBACK');
      throw error;
    }
  }
  nextEvidence() {
    return this.db.prepare('SELECT id, payload FROM context_outbox ORDER BY rowid LIMIT 1').get();
  }

  queueStaffingReceipt(id) {
    this.db.prepare('INSERT OR IGNORE INTO staffing_receipts(id) VALUES (?)').run(id);
  }

  nextStaffingReceipt(now) {
    return this.db.prepare('SELECT id FROM staffing_receipts WHERE done=0 AND checked<=? ORDER BY checked,rowid LIMIT 1').get(now);
  }

  trackTelegram(event) {
    this.db.prepare('INSERT OR IGNORE INTO office_telegram(id,thread) VALUES (?,?)').run(event.id, event.thread);
  }

  nextTelegram() {
    return this.db.prepare('SELECT * FROM office_telegram ORDER BY checked LIMIT 1').get();
  }

  telegramProgress(event, status, checked) {
    this.db.prepare('UPDATE office_telegram SET status=?,checked=? WHERE id=?').run(status, checked, event);
  }

  finishTelegram(event, outcomeKey, evidence) {
    this.db.exec('BEGIN IMMEDIATE');
    try {
      this.db.prepare('DELETE FROM office_telegram WHERE id=?').run(event);
      this.db.prepare('UPDATE assignment_reactions SET outcome_key=? WHERE assignment=?').run(outcomeKey, event);
      if (evidence) this.db.prepare('INSERT OR IGNORE INTO context_outbox VALUES (?,?)')
        .run(evidence.idempotency_key, JSON.stringify(evidence));
      this.db.exec('COMMIT');
    } catch (error) {
      this.db.exec('ROLLBACK');
      throw error;
    }
  }

  evidenceSent(id) {
    this.db.prepare('DELETE FROM context_outbox WHERE id=?').run(id);
  }

  nextReactionCleanup() {
    return this.db.prepare(`SELECT inbox.id, COALESCE(inbox.request, inbox.id) AS request,
                                  activity_id AS activityId, activity_removed AS activityRemoved,
                                  outcome_key AS outcomeKey, outcome_id AS outcomeId
                             FROM assignment_reactions JOIN inbox ON assignment=inbox.id
                             WHERE inbox.sent IS NOT NULL AND
                               NOT EXISTS(SELECT 1 FROM office_telegram WHERE office_telegram.id=inbox.id) AND
                               ((activity_id IS NOT NULL AND activity_removed=0) OR
                                (outcome_key IS NOT NULL AND outcome_id IS NULL))
                             ORDER BY inbox.rowid LIMIT 1`).get();
  }

  activityRemoved(event) {
    this.db.prepare('UPDATE assignment_reactions SET activity_removed=1 WHERE assignment=?').run(event);
  }

  outcomeSent(event, id) {
    this.db.prepare('UPDATE assignment_reactions SET outcome_id=? WHERE assignment=?').run(id, event);
  }
}
