import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';

const digest = parts => createHash('sha256').update(JSON.stringify(parts)).digest('hex');

export function executionEvidence({ roomId, actor, event, sent, status, now = new Date() }) {
  const trace = 'agents-thread-' + digest([roomId, event.thread]);
  return {
    event: 'agents:execution',
    workspace: 'agents-team',
    idempotency_key: 'agent-reply-' + digest([roomId, sent]),
    data: {
      session_id: trace, timestamp: now.toISOString(),
      evidence: { room: roomId, actor, reply: sent, outcome: status ?? 'progress' },
      execution: { trace_id: trace, task_id: 'agents-request-' + digest([roomId, event.request]),
        ...(!event.replyTo && { sequence: event.number }) },
    },
  };
}

export async function writeExecutionEvidence(payload) {
  const token = (await readFile('/context-token', 'utf8')).trim();
  const response = await fetch(process.env.CONTEXT_INTELLIGENCE_URL + '/events', {
    method: 'POST', redirect: 'error', signal: AbortSignal.timeout(8000),
    headers: { authorization: 'Bearer ' + token, 'content-type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error('Context Intelligence HTTP ' + response.status);
}
