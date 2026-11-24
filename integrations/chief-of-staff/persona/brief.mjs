import { readFile, writeFile } from 'node:fs/promises';
import { contextRequest } from '../direct/evidence.mjs';

const roles = {
  analyst: 'Analyst',
  coder: 'Coder',
  reviewer: 'Reviewer',
  manager: 'Manager',
  'remote-worker': 'Remote Worker',
};
const evidenceFile = '/run-data/observation.json';
const fields = ['facts', 'proposal', 'why', 'verification'];

async function collect(id) {
  const token = (await readFile('/reader-token', 'utf8')).trim();
  const reply = await contextRequest('/cypher', token, {
    workspace: 'agents-team',
    query: [
      'MATCH (e:ObservationRecordedEvent {workspace:$workspace, created_by:$author})',
      'WHERE e.node_id STARTS WITH $prefix RETURN e.data AS data LIMIT 2',
    ].join(' '),
    params: { author: 'chief-of-staff', prefix: id + '__' },
  });
  if (reply.results.length !== 1) throw new Error('Select one archived Chief of Staff observation');
  const note = JSON.parse(reply.results[0].data).observation;
  if (note?.observation_id !== id || note.kind !== 'observation') throw new Error('Unexpected observation');
  await writeFile(evidenceFile, JSON.stringify(note), { mode: 0o600, flag: 'wx' });
}

async function prompt(id, role) {
  const note = JSON.parse(await readFile(evidenceFile, 'utf8'));
  if (note.observation_id !== id) throw new Error('Unexpected evidence selection');
  const newRole = id.startsWith('cos-gap-');
  if (note.agent_key && note.agent_key !== role) throw new Error('Use the managed agent cited in the observation');
  if (newRole && note.proposal_type !== 'new') throw new Error('Choose a capability review that recommends a new role');
  const safe = {};
  for (const field of fields) {
    const value = note[field];
    if (typeof value !== 'string' || !value.trim() || value.length > 1200 ||
        /https?:|mxc:|file:|[a-f0-9]{24,}|[@#][^\s]+:[^\s]+/iu.test(value)) {
      throw new Error('Choose an observation without links, account identifiers or opaque tokens in its text');
    }
    safe[field] = value;
  }
  console.log([
    newRole ? 'Draft a small, reviewable persona for the proposed AI role named below.' :
      'Draft a small, reviewable persona adjustment for the existing AI role named below.',
    'Only propose behavior changes supported by this observation for the selected role.',
    'A workflow issue alone may call for shared procedure instead of a persona change.',
    'Use two concise rules, one in communication and one in working, with up to four rules if justified.',
    'Keep the wording in English. Existing policies, permission grants and approval requirements remain authoritative.',
    'Use communication sections voice, phrasing, structure, tone, context or editing.',
    'Use working sections framing, decisions, execution, collaboration, feedback or tradeoffs.',
    'Return JSON only: {"kind":"persona_draft","guidance":[{"collection":"communication","section":"structure","statement":"..."}]}.',
    'Use {"kind":"insufficient_evidence"} when the observed behavior does not support a narrow adjustment.',
    JSON.stringify({ role: roles[role] ?? role, observation: safe }),
  ].join('\n'));
}

try {
  const [action, role, id] = process.argv.slice(2);
  const gap = /^cos-gap-[0-9a-f]{64}$/.test(id);
  if (gap ? !/^[a-z0-9][a-z0-9_-]{0,63}$/.test(role) :
    (!/^[a-z0-9][a-z0-9_-]{0,63}$/.test(role) || !/^cos-(?:direct-)?[0-9a-f]{64}$/.test(id))) {
    throw new Error('Pass an agent role and a matching archived observation ID');
  }
  if (action === 'collect') await collect(id);
  else if (action === 'prompt') await prompt(id, role);
  else throw new Error('Use collect or prompt');
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
