import { digest, validateRoomId } from '../observation.mjs';

const eventPattern = /^\$[^\s/#?%]{1,255}$/u;
const userPattern = /^@[^\s:/#?%]{1,255}:[^\s/#?%]{1,255}$/u;
const kinds = new Set(['request', 'handoff', 'direct', 'reply', 'outcome', 'progress']);
const outcomes = new Set(['completed', 'blocked', 'undeclared']);
const workspace = 'agents-team';
const tracking = 'agents:interaction';

export function interaction(room, event, observer, owner) {
  const content = event.content;
  const relation = content?.['m.relates_to'];
  if (event.type !== 'm.room.message' || content?.msgtype !== 'm.text' ||
      event.sender === observer || !userPattern.test(event.sender) ||
      !eventPattern.test(event.event_id) || !Number.isSafeInteger(event.origin_server_ts) ||
      relation?.rel_type === 'm.replace') return null;

  const thread = relation?.rel_type === 'm.thread' && eventPattern.test(relation.event_id)
    ? relation.event_id : null;
  const mentions = Array.isArray(content['m.mentions']?.user_ids)
    ? [...new Set(content['m.mentions'].user_ids.filter(id => typeof id === 'string' && userPattern.test(id)))].slice(0, 16)
    : [];
  const declared = content['net.matrix_agents.task_outcome'];
  const turn = content['net.matrix_agents.turn'];
  const reply = thread && Number.isInteger(turn?.number) && eventPattern.test(turn.request);
  const explicit = reply && ['completed', 'blocked'].includes(declared?.status);
  const newRequest = !thread || (!reply && event.sender === owner && mentions.length > 0);
  const request = reply ? turn.request : newRequest ? event.event_id : null;
  const kind = newRequest ? 'request'
    : explicit ? 'outcome'
    : content['net.matrix_agents.direct_reply'] === true && reply ? 'direct'
    : reply && mentions.length === 1 ? 'handoff'
    : reply ? 'progress' : 'reply';

  return {
    room, event: event.event_id, thread: thread || event.event_id, at: event.origin_server_ts,
    sender: event.sender, mentions, kind,
    ...(request && { request }),
    ...(reply && turn.number > 0 && { sequence: turn.number }),
    outcome: explicit ? declared.status : 'undeclared',
    verified: Boolean(explicit && declared.status === 'completed' &&
      typeof declared.result === 'string' && declared.result.trim() &&
      typeof declared.verification === 'string' && declared.verification.trim()),
  };
}

export async function contextRequest(path, token, body) {
  const response = await fetch('http://context:8000' + path, {
    method: 'POST',
    redirect: 'error',
    headers: { authorization: 'Bearer ' + token, 'content-type': 'application/json' },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(8000),
  });
  if (!response.ok) throw new Error(`Context Intelligence ${path} returned HTTP ${response.status}`);
  return response.json();
}

export function writeInteraction(token, entry) {
  const session = 'agents-thread-' + digest([entry.room, entry.thread]);
  const request = entry.capability_gap?.request ?? entry.request;
  const execution = eventPattern.test(request ?? '') ? {
    trace_id: session,
    task_id: 'agents-request-' + digest([entry.room, request]),
    ...(Number.isInteger(entry.sequence) && { sequence: entry.sequence }),
  } : null;
  return contextRequest('/events', token, {
    event: tracking,
    workspace,
    idempotency_key: 'agents-event-' + digest([entry.room, entry.event]),
    data: {
      session_id: session,
      timestamp: new Date(entry.at).toISOString(),
      evidence: entry,
      ...(execution && { execution }),
    },
  });
}

export async function readInteractions(token, room) {
  validateRoomId(room);
  const result = await contextRequest('/cypher', token, {
    workspace,
    query: [
      'MATCH (e:AgentsInteractionEvent {workspace: $workspace, created_by: $author})',
      'WHERE e.occurred_at >= datetime($since)',
      'RETURN e.data AS data ORDER BY e.occurred_at DESC, e.node_id DESC LIMIT 600',
    ].join(' '),
    params: { author: 'chief-of-staff', since: new Date(Date.now() - 7 * 86400_000).toISOString() },
  });
  return result.results.map(row => JSON.parse(row.data).evidence).filter(entry => validEntry(entry, room));
}

function validEntry(entry, room) {
  return entry?.room === room && eventPattern.test(entry.event) && eventPattern.test(entry.thread) &&
    Number.isSafeInteger(entry.at) && userPattern.test(entry.sender) &&
    kinds.has(entry.kind) && outcomes.has(entry.outcome) && typeof entry.verified === 'boolean' &&
    Array.isArray(entry.mentions) && entry.mentions.every(id => userPattern.test(id));
}

export function reviewThreads(entries, room) {
  validateRoomId(room);
  const unique = new Map(entries.filter(entry => validEntry(entry, room)).map(entry => [entry.event, entry]));
  const groups = new Map();
  for (const entry of unique.values()) {
    const group = groups.get(entry.thread) || [];
    group.push(entry);
    groups.set(entry.thread, group);
  }
  return [...groups.entries()]
    .filter(([, events]) => events.some(entry => entry.kind !== 'request'))
    .sort(([, a], [, b]) => Math.max(...b.map(e => e.at)) - Math.max(...a.map(e => e.at)))
    .slice(0, 12)
    .map(([thread, events]) => ({
      task_id: digest([room, thread]),
      thread_url: `https://matrix.to/#/${encodeURIComponent(room)}/${encodeURIComponent(thread)}`,
      replies: events.sort((a, b) => a.at - b.at || a.event.localeCompare(b.event))
        .slice(-20).map((entry, index) => ({
          number: index + 1,
          agent: entry.sender,
          next_agent: entry.kind === 'handoff' ? entry.mentions[0] : null,
          ...(entry.kind === 'request' && { mentioned_recipients: entry.mentions }),
          reply_kind: entry.kind,
          outcome: entry.outcome,
          verification_present: entry.verified,
        })),
    }));
}

export function reviewPrompt(threads) {
  const aliases = new Map();
  function alias(id) {
    if (!id) return null;
    if (!aliases.has(id)) aliases.set(id, `participant-${aliases.size + 1}`);
    return aliases.get(id);
  }
  const samples = threads.map((thread, index) => ({
    task_id: `sample-${index + 1}`,
    turns: thread.replies.map(turn => ({
      number: turn.number,
      participant: alias(turn.agent),
      kind: turn.reply_kind,
      ...(turn.reply_kind === 'request' && {
        mentioned_recipients: (turn.mentioned_recipients || []).map(alias),
      }),
      ...(turn.reply_kind === 'handoff' && turn.next_agent && { handoff_to: alias(turn.next_agent) }),
      outcome: turn.outcome,
      verification_present: turn.verification_present,
    })),
  }));
  return [
    'Review only the supplied routing and outcome metadata from Matrix Agents. Answer in Italian.',
    'The window covers recent captured events and may omit older messages, keys or earlier turns.',
    'Request recipients come from explicit Matrix mentions. handoff_to appears only on a recorded agent handoff. A direct reply can answer an information request without a task outcome.',
    'A blocked or completed outcome is an explicit recorded declaration. No declaration in the window is insufficient to conclude that a task failed, succeeded or was abandoned.',
    'Use observed patterns of handoffs and outcome declarations. A verification_present value only records that verification was stated. Do not infer its quality or the contents of messages.',
    'Provide one narrow proposal with a measurable check if there is enough evidence. Use multiple threads for recurring claims. Include at least one exact sample ID and existing turn number in the sources array.',
    'Return JSON only in this format: {"kind":"observation","sources":[{"task_id":"sample-1","turns":[1]}],"facts":"...","proposal":"...","why":"...","verification":"..."}.',
    'If there is no justified proposal, return {"kind":"insufficient_evidence"}.',
    JSON.stringify(samples),
  ].join('\n');
}
