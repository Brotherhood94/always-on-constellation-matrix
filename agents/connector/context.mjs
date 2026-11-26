import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';

const digest = parts => createHash('sha256').update(JSON.stringify(parts)).digest('hex');

function officeEvidence({ roomId, actor, event, kind, matrixEvent, fields, now }) {
  const trace = 'agents-thread-' + digest([roomId, event.thread]);
  return {
    event: 'agents:' + kind,
    workspace: 'agents-team',
    idempotency_key: kind + '-' + digest([roomId, matrixEvent]),
    data: {
      // Scoped Context sessions have one contributor. Tasks can span multiple contributors.
      session_id: trace + '-' + digest([actor]).slice(0, 20), timestamp: now.toISOString(),
      evidence: { room: roomId, actor, thread: event.thread, request: event.request ?? event.id, ...fields },
      execution: { trace_id: trace, task_id: 'agents-request-' + digest([roomId, event.request ?? event.id]),
        ...(!event.replyTo && { sequence: event.number }) },
    },
  };
}

export function assignmentEvidence({ roomId, actor, event, now = new Date() }) {
  return officeEvidence({ roomId, actor, event, now, kind: 'assignment_accepted', matrixEvent: event.id,
    fields: { assignment: event.id, ...(event.delegator && { from: event.delegator }) } });
}

export function executionEvidence({ roomId, actor, event, sent, status, target, step, flow, policyRevision,
  evidence = [], receipt, need, now = new Date() }) {
  return officeEvidence({ roomId, actor, event, now, kind: 'execution', matrixEvent: sent,
    fields: { assignment: event.id, reply: sent, outcome: status ?? 'progress',
      ...(target && { to: target }), ...(step && { step }), ...(flow && { flow }),
      ...(flow === 'dispatch' && policyRevision && { policyRevision }), ...(evidence.length && { references: evidence }),
      ...(receipt && { operation: receipt }), ...(need && { need }) } });
}

export async function writeExecutionEvidence(payload, tokenPath = '/context-token') {
  const token = (await readFile(tokenPath, 'utf8')).trim();
  const response = await fetch(process.env.CONTEXT_INTELLIGENCE_URL + '/events', {
    method: 'POST', redirect: 'error', signal: AbortSignal.timeout(8000),
    headers: { authorization: 'Bearer ' + token, 'content-type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error('Context Intelligence HTTP ' + response.status);
}
