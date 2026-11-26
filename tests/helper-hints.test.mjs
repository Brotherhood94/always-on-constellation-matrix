import { DatabaseSync } from 'node:sqlite';
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtempSync, copyFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { dailyHints, hintPreference } from '../agents/connector/helper-hints.mjs';

test('English and Italian hint controls are explicit', () => {
  for (const command of ['Please stop sending me daily hints.', 'Smetti di mandarmi i suggerimenti.']) {
    assert.equal(hintPreference(command), false);
  }
  for (const command of ['Resume daily hints, please!', 'Riattiva i suggerimenti giornalieri.']) {
    assert.equal(hintPreference(command), true);
  }
  assert.equal(hintPreference('How do I stop sending hints?'), undefined);
});

test('daily delivery uses local time, retries with the same transaction after relocation, and skips missed days', async t => {
  const storage = mkdtempSync(join(tmpdir(), 'helper-hints-'));
  t.after(() => rmSync(storage, { recursive: true, force: true }));
  const original = join(storage, 'source.sqlite3');
  const relocated = join(storage, 'destination.sqlite3');
  const options = { timezone: 'Europe/Rome', features: ['tailnet'], choose: () => 0 };
  const db = new DatabaseSync(original);
  db.exec('CREATE TABLE settings (name TEXT PRIMARY KEY, value TEXT NOT NULL)');
  const source = dailyHints(db, options);
  const sends = [];
  const now = value => new Date(value);
  const send = (transaction, body) => {
    sends.push({ transaction, body });
    return '$fixture';
  };
  await source.tick(now('2026-11-26T08:59:59Z'), send, () => true);
  await source.tick(now('2026-11-26T09:00:00Z'), send, () => false);
  assert.equal(sends.length, 0);
  await assert.rejects(source.tick(now('2026-11-26T09:00:01Z'), (...args) => {
    send(...args);
    throw new Error('reply lost');
  }, () => true), /reply lost/);
  db.close();
  copyFileSync(original, relocated);
  const moved = new DatabaseSync(relocated);
  t.after(() => moved.close());
  const destination = dailyHints(moved, options);
  await destination.tick(now('2026-11-26T15:00:00Z'), send, () => true);
  assert.deepEqual(sends[0], sends[1]);
  assert.equal(sends[1].transaction, 'helper-daily-hint-2026-11-26');
  await destination.tick(now('2026-11-26T16:00:00Z'), send, () => true);
  destination.setEnabled(false);
  const restarted = dailyHints(moved, options);
  await restarted.tick(now('2026-11-27T10:00:00Z'), send, () => true);
  assert.equal(sends.length, 2);
  restarted.setEnabled(true);
  for (let day = 1; day < 7; day++) {
    await restarted.tick(now(`2026-12-0${day}T10:00:00Z`), send, () => true);
  }
  const next = sends.slice(1);
  assert.equal(next.length, 7);
  assert.equal(next[1].transaction, 'helper-daily-hint-2026-12-01');
  assert.equal(new Set(next.slice(0, 6).map(value => value.body.body)).size, 6);
  assert.ok(next.some(value => value.body.body.includes('install a remote worker')));
  assert.ok(next.some(value => value.body.body.includes('move an always-on agent')));
  assert.ok(next.every(value => !/Telegram|Dot/.test(value.body.body)));
});
