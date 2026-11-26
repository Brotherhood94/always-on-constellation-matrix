import { createHash } from 'node:crypto';
import { matchingAgents, requirement } from '../../../agents/connector/agent-catalog.mjs';

const eventId = /^\$[^\s/#?%]{1,254}$/u;
const maxExamples = 36;
const reviewInterval = 10 * 60_000;
const digest = (value) => createHash('sha256').update(JSON.stringify(value)).digest('hex');

export function staffingRoster(agents, integrations, server, now = Date.now()) {
  const services = integrations?.services;
  const state = services?.['Telegram Agent'] ?? services?.['Telegram Control'];
  if (!state || agents.some(agent => agent.key === 'telegram-agent')) return agents;
  const fresh = Number.isSafeInteger(integrations.time) && Math.abs(now - integrations.time * 1000) <= 180_000;
  const status = !fresh ? 'Status unavailable'
    : state === 'healthy' || (state === 'running' && services['Telegram engine'] === 'healthy') ? 'Available'
      : state === 'running' && services['Telegram engine'] !== 'unhealthy' ? 'Running'
        : state === 'starting' ? 'Starting' : 'Unavailable';
  return [...agents, {
    key: 'telegram-agent', id: '@telegram-agent:' + server, name: 'Telegram Agent', role: 'worker', status,
    limits: 'Uses the scoped Telegram handoff. Draft delivery requires operator approval and a verified chat binding.',
    persona: { purpose: 'Link Telegram chats through the bridge, prepare drafts, and read verified chat updates.' },
    capabilities: [
      { code: 'telegram-chat-link', description: 'Find and link verified Telegram contacts and groups.',
        resources: ['workspace:telegram', 'telegram:*'] },
      { code: 'telegram-draft', description: 'Prepare a draft for operator approval in Telegram Bridge.',
        resources: ['workspace:telegram', 'telegram:*'] },
      { code: 'telegram-chat-update', description: 'Summarize recent messages from a verified Telegram chat.',
        resources: ['workspace:telegram', 'telegram:*'] },
    ],
  }];
}

export function staffingExamples(rows, roster) {
  const contributors = new Set(roster.map(agent => agent.id));
  const catalog = { agents: roster.filter(agent => Array.isArray(agent.capabilities)) };
  const examples = [];
  const requestsByPattern = new Map();
  for (const row of rows) {
    const { need, request, reply, thread } = row;
    if (need?.kind !== 'role' || row.status === 'verified' || row.outcome === 'completed' ||
        !contributors.has(row.actor) || ![request, reply, thread].every(id => typeof id === 'string' && eventId.test(id))) continue;
    const skill = requirement({ capability: need.topic, resource: need.resource });
    if (!skill || matchingAgents(catalog, skill).length) continue;
    const reason = typeof need.reason === 'string' ? need.reason.replace(/\s+/gu, ' ').trim().slice(0, 400) : '';
    const pattern = skill.capability + '/' + skill.resource;
    const requests = requestsByPattern.get(pattern) ?? new Set();
    if (requests.size === 4 || requests.has(request)) continue;
    requests.add(request);
    requestsByPattern.set(pattern, requests);
    examples.push({ pattern, evidence: row, description: {
      capability: skill.capability, resource_type: skill.resource.split(':', 1)[0], reason,
    } });
  }
  return examples;
}

export function staffingClusters(answer, examples) {
  if (!Array.isArray(answer?.clusters) || answer.clusters.length > Math.floor(examples.length / 2)) {
    throw new Error('Invalid staffing clusters');
  }
  const seen = new Set();
  const groups = [];
  for (const cluster of answer.clusters) {
    const { duty, ids } = cluster ?? {};
    if (typeof duty !== 'string' || !duty.trim() || duty.length > 140 || /[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]/u.test(duty) ||
        !Array.isArray(ids) || ids.length < 2 || ids.length > examples.length) throw new Error('Invalid staffing cluster');
    const members = ids.map(id => {
      if (!Number.isSafeInteger(id) || id < 1 || id > examples.length || seen.has(id)) {
        throw new Error('Invalid or duplicate staffing example ID');
      }
      seen.add(id);
      return examples[id - 1];
    });
    const patterns = [...new Set(members.map(({ pattern }) => pattern))].sort();
    const assignments = new Map();
    for (const { evidence } of members) {
      if (!assignments.has(evidence.request)) assignments.set(evidence.request, evidence);
    }
    if (patterns.length < 2 || assignments.size < 2) continue;
    groups.push({ patterns, job: { mode: 'semantic-gap', topic: duty.trim(), evidence: [...assignments.values()].slice(0, 4) } });
  }
  return groups;
}

const prompt = [
  'Group the following explicitly reported Office staffing gaps by the same specific uncovered duty.',
  'Two gaps belong together only if one narrowly defined specialist could take responsibility for both.',
  'Similar wording, a shared project or a broad field such as engineering is insufficient.',
  'Different project scopes and capability names can describe the same duty. Use the reasons to decide.',
  'Skip uncertain cases, overlaps, routing issues, missing tools and credentials, and temporary outages.',
  'Each numeric ID refers to one evidence record. Include at least two distinct assignments in each group.',
  'Return {"clusters":[{"duty":"short description of the same duty","ids":[1,2]}]} or {"clusters":[]}.',
  'Use only supplied IDs, each at most once. Only classify evidence. Do not use tools or propose hires.',
].join(' ');

export function createStaffingReview(db, queue) {
  db.exec('CREATE TABLE IF NOT EXISTS desk_staffing_patterns (pattern TEXT PRIMARY KEY)');
  const marked = db.prepare('SELECT 1 FROM desk_staffing_patterns WHERE pattern=?');
  const existing = db.prepare('SELECT 1 FROM desk_jobs WHERE id=?');
  const mark = db.prepare('INSERT OR IGNORE INTO desk_staffing_patterns VALUES (?)');
  const read = db.prepare('SELECT value FROM desk_clock WHERE id=?');
  const write = db.prepare('INSERT OR REPLACE INTO desk_clock (id,value) VALUES (?,?)');
  // Older exact reviews are already recorded under their original job IDs.
  const covered = pattern => marked.get(pattern) || existing.get('hire:' + pattern);
  const put = (id, job, patterns) => {
    if (!patterns.some(covered)) queue(id, job);
    for (const pattern of patterns) mark.run(pattern);
  };
  const transaction = operation => {
    db.exec('BEGIN IMMEDIATE');
    try {
      operation();
      db.exec('COMMIT');
    } catch (error) {
      db.exec('ROLLBACK');
      throw error;
    }
  };

  return {
    async review(examples, exactJobs, ask, now = Date.now()) {
      const pending = examples.filter(item => !covered(item.pattern));
      const anchors = examples.filter(item => covered(item.pattern));
      const selected = [...pending.slice(0, maxExamples - Math.min(anchors.length, 8)), ...anchors.slice(0, 8)];
      let signature;
      let groups = [];
      let failure;
      if (pending.length && new Set(selected.map(({ pattern }) => pattern)).size >= 2 &&
          new Set(selected.map(({ evidence }) => evidence.request)).size >= 2) {
        signature = digest(selected.map(({ pattern, evidence, description }) =>
          digest([pattern, evidence.request, description])).sort());
        if (signature === read.get('staffing-last-batch')?.value) signature = undefined;
      }
      if (signature) {
        if (now - Number(read.get('staffing-last-attempt')?.value ?? -Infinity) < reviewInterval) return false;
        const assignments = new Map();
        const input = selected.map(({ evidence, description }, index) => {
          if (!assignments.has(evidence.request)) assignments.set(evidence.request, assignments.size + 1);
          return { id: index + 1, assignment: assignments.get(evidence.request), ...description };
        });
        write.run('staffing-last-attempt', String(now));
        try {
          groups = staffingClusters(await ask(prompt, input), selected);
        } catch (error) {
          failure = error;
        }
      }
      if (exactJobs.size || signature) {
        transaction(() => {
          for (const { patterns, job } of groups) put('hire:semantic:' + digest(patterns), job, patterns);
          for (const [id, job] of exactJobs) put(id, job, [job.topic]);
          if (signature && !failure) write.run('staffing-last-batch', signature);
        });
      }
      if (failure) throw failure;
      return true;
    },
  };
}
