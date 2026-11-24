import { gapKey, matchingAgents, requirement } from '../../agent-catalog.mjs';
import { mentionMessage } from '../../matrix-format.mjs';
import { digest } from '../observation.mjs';

const eventId = /^\$[^\s/#?%]{1,255}$/u;

export function confirmedGap(event, observer, snapshot) {
  const content = event.content;
  const claim = content?.[gapKey];
  const wanted = requirement(claim);
  const relation = content?.['m.relates_to'];
  const mentions = content?.['m.mentions']?.user_ids;
  if (!wanted || content.msgtype !== 'm.text' || relation?.rel_type !== 'm.thread' ||
      !eventId.test(relation.event_id ?? '') || !eventId.test(relation['m.in_reply_to']?.event_id ?? '') ||
      !eventId.test(claim.request ?? '') || !Array.isArray(mentions) || !mentions.includes(observer) ||
      typeof claim.reason !== 'string' || !claim.reason.trim() || claim.reason.length > 400 ||
      !snapshot.agents.some(agent => agent.id === event.sender && agent.role !== 'observer')) return null;
  return { ...wanted, request: claim.request, reason: claim.reason.trim() };
}

export function recommendation(rows, requested, snapshot) {
  const matches = matchingAgents(snapshot, requested);
  if (matches.length) return { kind: 'existing', name: matches[0].name };
  if (new Set(rows.map(row => row.request)).size >= 2) return { kind: 'new' };
  return { kind: 'collect' };
}

export function capabilityReview(db, room, owner) {
  db.exec(`
    CREATE TABLE IF NOT EXISTS capability_evidence (
      event TEXT PRIMARY KEY, gap TEXT NOT NULL, request TEXT NOT NULL,
      thread TEXT NOT NULL, stamp INTEGER NOT NULL, data TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS capability_evidence_gap ON capability_evidence(gap, stamp);
    CREATE TABLE IF NOT EXISTS capability_reports (
      id TEXT PRIMARY KEY, gap TEXT NOT NULL, created INTEGER NOT NULL, data TEXT NOT NULL,
      matrix_event TEXT, archived INTEGER NOT NULL DEFAULT 0
    );
  `);
  const insert = db.prepare('INSERT OR IGNORE INTO capability_evidence VALUES (?, ?, ?, ?, ?, ?)');
  const rowsFor = db.prepare('SELECT * FROM capability_evidence WHERE gap=? ORDER BY stamp DESC, event');
  const groups = db.prepare('SELECT gap FROM capability_evidence GROUP BY gap ORDER BY MAX(stamp) DESC');
  const readReport = db.prepare('SELECT * FROM capability_reports WHERE id=?');
  const first = db.prepare('SELECT matrix_event FROM capability_reports WHERE gap=? AND matrix_event IS NOT NULL ORDER BY created, id LIMIT 1');
  const queue = db.prepare('INSERT OR IGNORE INTO capability_reports (id, gap, created, data) VALUES (?, ?, ?, ?)');
  const pending = db.prepare('SELECT * FROM capability_reports WHERE archived=0 ORDER BY created, id LIMIT 4');
  const sent = db.prepare('UPDATE capability_reports SET matrix_event=? WHERE id=?');
  const archived = db.prepare('UPDATE capability_reports SET archived=1 WHERE id=?');

  return {
    collect(entry) {
      const gap = entry.capability_gap;
      insert.run(entry.event, digest([gap.capability, gap.resource]), gap.request, entry.thread, entry.at, JSON.stringify(gap));
    },
    plan(snapshot) {
      for (const group of groups.all()) {
        const rows = rowsFor.all(group.gap);
        const gap = JSON.parse(rows[0].data);
        const result = recommendation(rows, gap, snapshot);
        const id = digest([group.gap, result.kind]);
        if (readReport.get(id)) continue;
        const original = first.get(group.gap)?.matrix_event;
        const distinct = [...new Map(rows.map(row => [row.request, row])).values()];
        const sources = distinct.slice(0, 4).map(row => ({
          thread_url: `https://matrix.to/#/${encodeURIComponent(room)}/${encodeURIComponent(row.thread)}`,
        }));
        const links = sources.map(source => `[Agents thread](${source.thread_url})`);
        const next = result.kind === 'existing' ? `**Recommendation** Use the existing profile ${result.name}, subject to availability and the requested scope.` :
          result.kind === 'new' ? `**Recommendation** Consider an agent for ${gap.capability} on ${gap.resource}. Grant only the access needed for this scope. Check it with a representative task from the linked threads and verify the expected result.` :
            '**Recommendation** Defer creating an agent. Confirm that similar requests recur and check whether an existing role can cover the scope.';
        const body = `**Capability review**\n\n- **Capability** ${gap.capability}\n- **Scope** ${gap.resource}\n` +
          `- **Evidence** ${distinct.length} distinct request(s). ${links.join(' · ')}\n\n${next}` +
          (result.kind === 'new' ? `\n\nObservation ID: \`cos-gap-${id}\`` : '');
        const target = { id: owner, name: owner.slice(1).split(':')[0] };
        const content = { ...mentionMessage(body, target), ...(original && { 'm.relates_to': {
          rel_type: 'm.thread', event_id: original, is_falling_back: true, 'm.in_reply_to': { event_id: original },
        } }) };
        const category = gap.resource.split(':')[0];
        const observation = { kind: 'observation', observation_id: 'cos-gap-' + id, proposal_type: result.kind, sources,
          facts: `${distinct.length} distinct requests required the ${gap.capability} capability in the ${category} resource category.`,
          proposal: result.kind === 'new' ? 'Consider a dedicated agent with narrowly scoped access.' :
            'Use an existing role or collect more evidence before changing the team.',
          why: 'The proposal reflects the capability catalog and the count of distinct requests.',
          verification: 'Run a representative task with the expected outcome and check that it stays within the intended scope.',
        };
        queue.run(id, group.gap, Date.now(), JSON.stringify({ content, observation }));
      }
    },
    pending: () => pending.all(),
    sent: (id, event) => sent.run(event, id),
    archived: id => archived.run(id),
  };
}
