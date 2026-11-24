import { createHash } from 'node:crypto';

export const digest = text => createHash('sha256').update(text).digest('hex');
export const operationId = /^[a-f0-9]{32}$/u;
export const fingerprint = /^[a-f0-9]{64}$/u;
export const eventId = /^\$[^\s\x00-\x1f]{1,254}$/u;
export const reviewWindow = 15 * 60_000;
export const pollStart = 'org.matrix.msc3381.poll.start';
export const pollEnd = 'org.matrix.msc3381.poll.end';
export const pollText = 'org.matrix.msc1767.text';
export const agentTurnKey = 'net.matrix_agents.turn';
const replyLimit = 6;
const pollResponse = 'org.matrix.msc3381.poll.response';
export const pollChoice = (task, action) => digest(JSON.stringify([task.id, task.hash, task.proposal_event, action]));

export function requestText(event, root, maximumBytes = 1800) {
  const { type, content } = event;
  const mentions = content?.['m.mentions'];
  if (type !== 'm.room.message' || content?.msgtype !== 'm.text' || typeof content.body !== 'string' ||
      content['m.new_content'] || mentions?.room || mentions?.user_ids?.length !== 1 ||
      mentions.user_ids[0] !== root) return null;
  const relation = content['m.relates_to'];
  if (relation && (relation.rel_type !== 'm.thread' || !eventId.test(relation.event_id))) return null;
  const text = content.body.replace(/^(?:>[^\n]*\n)+\n/u, '')
    .replace(/^\s*@?root(?::[a-z0-9.-]+)?[,:\s]+/iu, '').trim();
  return text && Buffer.byteLength(text) <= maximumBytes ? text : null;
}

export function pollVote(event) {
  if (![pollResponse, 'm.poll.response'].includes(event.type)) return null;
  const relation = event.content?.['m.relates_to'];
  const choices = event.content?.[event.type]?.answers ?? event.content?.['m.selections'];
  if (relation?.rel_type !== 'm.reference' || typeof relation.event_id !== 'string' || !eventId.test(relation.event_id) ||
      !Array.isArray(choices) || choices.length !== 1 || typeof choices[0] !== 'string' || !fingerprint.test(choices[0])) return null;
  return { poll: relation.event_id, choice: choices[0] };
}

function canDecide(config, task, room, now) {
  return Boolean(room === config.audit && task?.proposal_event && task.poll_event &&
    !task.action && !task.outcome && now < task.expires);
}

export function pollDecision(config, task, vote, sender, room, now) {
  if (!canDecide(config, task, room, now) || sender !== config.owner ||
      vote.poll !== task.poll_event || typeof task.command !== 'string' || digest(task.command) !== task.hash) return null;
  return ['approve', 'reject'].find(action => pollChoice(task, action) === vote.choice) ?? null;
}

function reviewText(value, limit) {
  const line = typeof value === 'string' ? value.replace(/[ \t\r\n\u2028\u2029]+/gu, ' ').trim() : '';
  return line.length <= limit && !/[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]/u.test(line) ? line : '';
}

export function proposal(text, operations) {
  const answer = JSON.parse(text);
  if (!answer || typeof answer !== 'object' || Array.isArray(answer)) throw new Error('Invalid selection.');
  const summary = reviewText(answer.summary, 220);
  if (answer.operation === null) return { summary: summary || 'The request needs a registered operation.' };
  if (typeof answer.operation !== 'string' || !Object.hasOwn(operations, answer.operation)) {
    throw new Error('Unknown registered operation.');
  }
  const definition = operations[answer.operation];
  const command = definition.argv.join(' ');
  return {
    operation: answer.operation, summary: definition.purpose, command, hash: digest(command),
    details: { ...definition, request_summary: summary },
  };
}

const executionCauses = new Map(Object.entries({
  launch_failed: 'The host executor could not start the script.',
  timed_out: 'The script exceeded its execution time limit.',
  executor_rejected: 'The host executor returned an error before confirming the outcome.',
  unknown: 'The host executor did not confirm an outcome.',
}));

export function executionCause(task) {
  if (task.outcome === 'proposal_failed') return task.summary;
  if (task.outcome !== 'failed') return executionCauses.get(task.outcome);
  if (Number.isInteger(task.exit_code) && task.exit_code < 0) {
    return `The script was terminated by signal ${-task.exit_code}.`;
  }
  const reasons = JSON.parse(task.review_details ?? '{}').failure_reasons;
  return reasons?.[task.exit_code] ?? 'The script returned an exit code without identifying a specific cause.';
}

export function sourceTurn(content, config, event) {
  const turn = content?.[agentTurnKey];
  if (content?.['m.relates_to']?.rel_type !== 'm.thread' || typeof turn?.request !== 'string' ||
      !eventId.test(turn.request) || !Number.isInteger(turn.number) || turn.number < 1 || turn.number >= replyLimit) return null;
  const base = { request: turn.request, number: turn.number };
  const managed = turn.managed;
  if (!managed) return base;
  const manager = '@manager:' + config.root.split(':').slice(1).join(':');
  if (managed.version !== 2 || managed.target !== config.root || managed.manager !== manager) return base;
  const dispatch = turn.number === 1 && event.sender === manager ? event.event_id : managed.dispatch;
  return typeof dispatch === 'string' && eventId.test(dispatch) ? { ...base, managed: { manager, dispatch } } : base;
}

export function resultHandoff(config, task, turn) {
  if (!turn || [config.owner, config.root].includes(task.sender)) return null;
  const next = turn.number + 1;
  if (next >= replyLimit) return null;
  if (!turn.managed) return { recipient: task.sender, turn: { request: turn.request, number: next } };
  const { manager, dispatch } = turn.managed;
  const recipient = task.action === 'approve' && task.outcome !== 'completed' && next < replyLimit - 1
    ? task.sender : manager;
  return {
    recipient,
    turn: { request: turn.request, number: next, managed: { version: 2, manager, dispatch, target: recipient } },
  };
}

export function auditRecord(config, entry, stage, time, sequence) {
  return {
    event: 'root:' + stage,
    workspace: 'root-operations',
    idempotency_key: 'root-' + entry.id + '-' + stage,
    data: {
      session_id: 'root-' + entry.id,
      timestamp: new Date(time).toISOString(),
      execution: { trace_id: entry.source_thread, task_id: entry.event, sequence },
      operation: {
        id: entry.id, type: entry.operation, executed_on: 'macai', action: entry.action,
        outcome: entry.outcome, command_sha256: entry.hash, requested_by: entry.sender,
        decided_by: entry.approver, request_event_id: entry.event, decision_event_id: entry.decision,
        source_room_id: entry.room, review_room_id: config.audit,
        review_event_id: entry.proposal_event, exit_code: entry.exit_code,
      },
    },
  };
}

export function matrixLink(room, event) {
  return 'https://matrix.to/#/' + encodeURIComponent(room) + '/' + encodeURIComponent(event);
}

export function privateReview(state, members) {
  const value = type => state.find(event => event.type === type && event.state_key === '')?.content;
  const active = state.filter(event => event.type === 'm.room.member' && ['join', 'invite'].includes(event.content?.membership));
  const joined = active.filter(event => event.content.membership === 'join').map(event => event.state_key);
  return value('m.room.create')?.['m.federate'] === false && value('m.room.join_rules')?.join_rule === 'invite' &&
    value('m.room.encryption')?.algorithm === 'm.megolm.v1.aes-sha2' &&
    value('m.room.history_visibility')?.history_visibility === 'joined' && !value('m.room.tombstone') &&
    active.length === members.length && members.every(member => joined.includes(member));
}
