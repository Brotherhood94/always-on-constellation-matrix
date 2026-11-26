import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { test } from 'node:test';
import { createStaffingReview, staffingClusters, staffingExamples } from './staffing-clusters.mjs';

const actor = '@coordinator:fixture.test';
const roster = [{ id: actor, capabilities: [] }];
const minute = 60_000;
const now = Date.parse('2026-11-26T10:00:00Z');
const gap = (id, topic, resource, reason, fields = {}) => ({
  actor, request: '$' + id, thread: '$' + id, reply: '$reply-' + id, status: 'blocked',
  need: { kind: 'role', topic, resource, reason }, ...fields,
});

const notes = gap('first', 'release-notes', 'repo:fixture/alpha', 'Publish the customer-facing summary for a release.');
const summary = gap('second', 'customer-change-summary', 'repo:fixture/beta', 'Explain the changes to customers in the release announcement.');
const security = gap('third', 'review-dependency-risk', 'repo:fixture/alpha', 'Assess security impact of dependency updates.');
const noExact = new Map();
const sameDuty = { clusters: [{ duty: 'Write customer-facing release notes', ids: [1, 2] }] };

function journal(t) {
  const db = new DatabaseSync(':memory:');
  t.after(() => db.close());
  db.exec('CREATE TABLE desk_jobs (id TEXT PRIMARY KEY, body TEXT); CREATE TABLE desk_clock (id TEXT PRIMARY KEY, value TEXT);');
  const queue = (id, body) => db.prepare('INSERT OR IGNORE INTO desk_jobs VALUES (?,?)').run(id, JSON.stringify(body));
  const jobs = () => db.prepare('SELECT * FROM desk_jobs ORDER BY rowid').all().map(row => ({ ...row, body: JSON.parse(row.body) }));
  return { db, queue, jobs };
}

test('only unresolved, uncovered staffing evidence from known contributors reaches the model', () => {
  const covered = gap('covered', 'review-dependency-risk', 'repo:fixture/beta', 'Perform a risk review.');
  const specialist = { id: '@reviewer:fixture.test', active: false,
    capabilities: [{ code: 'review-dependency-risk', resources: ['repo:*'] }] };
  const repeated = { ...notes, reply: '$duplicate' };
  const examples = staffingExamples([notes, repeated, summary, covered, security,
    { ...security, actor: '@other:fixture.test' }, { ...summary, status: 'verified' },
    { ...notes, outcome: 'completed' }, { ...notes, need: { ...notes.need, kind: 'tool' } },
    { ...notes, need: { ...notes.need, kind: 'credentials' } }, { ...notes, need: { ...notes.need, kind: 'outage' } }],
  [...roster, specialist]);
  assert.deepEqual(examples.map(item => item.evidence.request), ['$first', '$second']);
  assert.deepEqual(examples[0].description, {
    capability: 'release-notes', resource_type: 'repo', reason: notes.need.reason,
  });
});

test('semantic clusters accept paraphrases across scopes and must cite multiple assignments', () => {
  const examples = staffingExamples([notes, summary, security], roster);
  const [group] = staffingClusters(sameDuty, examples);
  assert.deepEqual(group.patterns, ['customer-change-summary/repo:fixture/beta', 'release-notes/repo:fixture/alpha']);
  assert.deepEqual(group.job.evidence.map(row => row.request), ['$first', '$second']);
  assert.equal(group.job.topic, 'Write customer-facing release notes');
  for (const ids of [[1, 4], [1, 1], ['1', 2]]) {
    assert.throws(() => staffingClusters({ clusters: [{ duty: 'Candidate duty', ids }] }, examples), /staffing example ID/u);
  }
  const sameAssignment = staffingExamples([notes, { ...summary, request: notes.request }], roster);
  assert.deepEqual(staffingClusters(sameDuty, sameAssignment), []);
  const samePattern = staffingExamples([notes, { ...notes, request: summary.request }], roster);
  assert.deepEqual(staffingClusters(sameDuty, samePattern), []);
});

test('semantic review persists, uses scoped evidence, and suppresses duplicate exact and semantic proposals', async t => {
  const { db, queue, jobs } = journal(t);
  const samples = staffingExamples([notes, summary, security], roster);
  let review = createStaffingReview(db, queue);
  let calls = 0;
  await review.review(samples, noExact, async (prompt, data) => {
    calls++;
    assert.match(prompt, /same specific uncovered duty/u);
    assert.deepEqual(data.map(item => [item.id, item.assignment]), [[1, 1], [2, 2], [3, 3]]);
    assert.ok(!JSON.stringify(data).includes('fixture/alpha'));
    return sameDuty;
  }, now);
  assert.equal(jobs().length, 1);
  assert.match(jobs()[0].id, /^hire:semantic:[a-f0-9]{64}$/u);
  assert.deepEqual(jobs()[0].body.evidence, [notes, summary]);

  // Recreate the reviewer as it would be after a connector restart.
  review = createStaffingReview(db, queue);
  await review.review([...samples].reverse(), noExact, async () => { calls++; }, now + 10 * minute);
  assert.equal(calls, 1);
  const original = samples[0].pattern;
  await review.review([], new Map([['hire:' + original, { mode: 'gap', topic: original, evidence: [notes] }]]));
  const another = gap('fourth', 'release-brief', 'repo:fixture/gamma', 'Write a summary for customers to understand this release.');
  const updated = staffingExamples([another, notes], roster);
  await review.review(updated, noExact, async () => sameDuty, now + 20 * minute);
  const alias = updated[0].pattern;
  await review.review([], new Map([['hire:' + alias, { mode: 'gap', topic: alias, evidence: [another] }]]));
  assert.equal(jobs().length, 1);
});

test('an existing exact review remains authoritative, and failed classification is throttled and retried', async t => {
  const { db, queue, jobs } = journal(t);
  const examples = staffingExamples([notes, summary], roster);
  const original = examples[0].pattern;
  queue('hire:' + original, { mode: 'gap', topic: original });
  const review = createStaffingReview(db, queue);
  let calls = 0;
  await assert.rejects(review.review(examples, noExact, async () => { calls++; throw new Error('ACP unavailable'); }, now), /ACP unavailable/u);
  await review.review(examples, noExact, async () => { calls++; }, now + minute);
  assert.equal(calls, 1);
  assert.equal(jobs().length, 1);
  await review.review(examples, noExact, async () => { calls++; return sameDuty; }, now + 10 * minute);
  assert.equal(calls, 2);
  assert.deepEqual(jobs().map(job => job.id), ['hire:' + original]);
});

test('a single assignment cannot trigger a model turn even when it reports multiple gaps', async t => {
  const { db, queue, jobs } = journal(t);
  const review = createStaffingReview(db, queue);
  const examples = staffingExamples([notes, { ...summary, request: notes.request }], roster);
  await review.review(examples, noExact, async () => assert.fail('unexpected model turn'), now);
  assert.deepEqual(jobs(), []);
});

test('overlapping exact groups produce one semantic proposal and exact review survives a classifier failure', async t => {
  const repeats = [
    notes, { ...notes, request: '$repeat-notes' },
    summary, { ...summary, request: '$repeat-summary' },
  ];
  const examples = staffingExamples(repeats, roster);
  const exact = new Map([examples[0], examples[2]].map(({ pattern }) =>
    ['hire:' + pattern, { mode: 'gap', topic: pattern }]));
  const merged = journal(t);
  await createStaffingReview(merged.db, merged.queue).review(examples, exact, async () =>
    ({ clusters: [{ duty: 'Publish customer-facing release notes', ids: [1, 2, 3, 4] }] }), now);
  assert.equal(merged.jobs().length, 1);
  assert.equal(merged.jobs()[0].body.mode, 'semantic-gap');

  const fallback = journal(t);
  const review = createStaffingReview(fallback.db, fallback.queue);
  await assert.rejects(review.review(examples, exact, async () => {
    throw new Error('ACP unavailable');
  }, now), /ACP unavailable/u);
  assert.deepEqual(fallback.jobs().map(job => job.id), [...exact.keys()]);
});
