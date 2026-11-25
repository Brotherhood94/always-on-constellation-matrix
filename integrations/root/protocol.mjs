import { replyLimit } from '../../agents/connector/office-policy.mjs';
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
export const reviewedScriptOperation = 'reviewed-script';
export const reviewedPlanOperation = 'reviewed-plan';
export const maximumScriptBytes = 4000;
const reviewPrefix = 'ROOT_REVIEW:';
const maximumReviewRequestBytes = 12000;

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
    .replace(/^\s*@?(?:vps-manager|VPS Manager|root)(?::[a-z0-9.-]+)?[,:\s]+/iu, '').trim();
  const limit = reviewLine(text) === null ? maximumBytes : maximumReviewRequestBytes;
  return text && Buffer.byteLength(text) <= limit ? text : null;
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

function exitReasons(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
  return Object.fromEntries(Object.entries(value).filter(([code]) => /^[1-9]\d{0,2}$/u.test(code) && Number(code) <= 125)
    .map(([code, reason]) => [code, reviewText(reason, 160)]).filter(([, reason]) => reason).slice(0, 8));
}

export class ProposalError extends Error {}

function readProposal(text, allowFormatting) {
  let json = text.trim();
  if (allowFormatting && !json.startsWith('{')) {
    const fenceLines = json.match(/^[ \t]*```[^\r\n]*$/gmu) ?? [];
    if (fenceLines.length === 2) {
      const fenced = /(?:^|\n)[ \t]*```(?:json)?[ \t]*\r?\n([\s\S]*?)\r?\n[ \t]*```$/iu.exec(json);
      if (fenced) json = fenced[1];
    } else if (!fenceLines.length) {
      const afterIntro = /(?:^|\n)([ \t]*\{[\s\S]*\})$/u.exec(json);
      if (afterIntro) json = afterIntro[1];
    }
  }
  let value;
  try {
    value = JSON.parse(json);
  } catch {
    throw new ProposalError('Expected one JSON proposal. The response could not be parsed.');
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new ProposalError('Expected one JSON proposal object.');
  }
  return value;
}

function reviewLine(text) {
  const lastLine = text.slice(text.lastIndexOf('\n') + 1);
  if (lastLine.startsWith('VPS_REVIEW:')) return lastLine.slice('VPS_REVIEW:'.length);
  return lastLine.startsWith(reviewPrefix) ? lastLine.slice(reviewPrefix.length) : null;
}

function reviewedPlan(answer) {
  const { plan } = answer;
  if (!plan || !['shell', 'inspect-file', 'replace-file'].includes(plan.kind)) {
    throw new ProposalError('Specify a shell, inspect-file or replace-file maintenance plan.');
  }
  const exact = { kind: plan.kind };
  if (plan.kind !== 'shell') {
    if (typeof plan.path !== 'string' || plan.path.length > 512 || !/^\/[A-Za-z0-9_./@+=-]+$/u.test(plan.path)) {
      throw new ProposalError('Specify one absolute file path.');
    }
    exact.path = plan.path;
  }
  if (plan.kind === 'replace-file') {
    if (!['before', 'after'].every(key => typeof plan[key] === 'string' && Buffer.byteLength(plan[key]) <= 4096 &&
        !plan[key].includes('\0')) || plan.before === plan.after) {
      throw new ProposalError('Include the exact original and replacement text.');
    }
    exact.before = plan.before;
    exact.after = plan.after;
  }
  const scriptFields = plan.kind === 'shell' ? ['script', 'verify'] : plan.kind === 'replace-file' ? ['verify'] : [];
  for (const key of scriptFields) {
    if (typeof plan[key] !== 'string' || !plan[key].trim() || !/^[\t\n\x20-\x7e]{1,4000}$/u.test(plan[key])) {
      throw new ProposalError('Include the exact commands and a separate verification script.');
    }
    exact[key] = plan[key];
  }
  const command = JSON.stringify(exact);
  const summary = reviewText(answer.summary, 220);
  const impact = reviewText(answer.impact, 220);
  const verification = reviewText(answer.verification, 220);
  const recovery = reviewText(answer.recovery, 220);
  if (!summary || !impact || !verification || (plan.kind !== 'inspect-file' && !recovery) ||
      Buffer.byteLength(command) > 15000) throw new ProposalError('Review requires purpose, impact, verification and recovery.');
  return { operation: reviewedPlanOperation, summary, command, hash: digest(command),
    details: { impact, verification, recovery } };
}

export function reviewBody(task) {
  if (task.operation !== reviewedPlanOperation) return task.command;
  const plan = JSON.parse(task.command);
  if (plan.kind === 'shell') return 'Apply\n' + plan.script + '\n\nVerify\n' + plan.verify;
  if (plan.kind === 'inspect-file') return 'Inspect regular file\n' + plan.path + '\nReturn a bounded redacted preview.';
  return 'Replace ' + plan.path + '\n' +
    'Remove exact text: ' + JSON.stringify(plan.before) + '\n' +
    'Insert exact text: ' + JSON.stringify(plan.after) + '\n\nVerify\n' + plan.verify +
    '\nIf verification fails, restore the previous bytes if the replacement is still unchanged.';
}

function reviewedProposal(answer) {
  const command = answer.script;
  if (typeof command !== 'string' || !command.trim()) {
    throw new ProposalError('The proposal is missing the exact Bash script.');
  }
  if (Buffer.byteLength(command) > maximumScriptBytes) {
    throw new ProposalError('The Bash script exceeds the 4000 byte review limit.');
  }
  if (/[^\x09\x0a\x20-\x7e]/u.test(command)) {
    throw new ProposalError('The Bash script must contain only visible ASCII, newlines and tabs.');
  }
  const summary = reviewText(answer.summary, 220);
  const impact = reviewText(answer.impact, 220);
  const verification = reviewText(answer.verification, 220);
  for (const [name, value] of Object.entries({ summary, impact, verification })) {
    if (!value) throw new ProposalError('The proposal needs a valid ' + name + ' field (up to 220 characters).');
  }
  const recovery = reviewText(answer.recovery, 220);
  return {
    operation: reviewedScriptOperation, summary, command, hash: digest(command),
    details: { impact, verification, ...(recovery && { recovery }), failure_reasons: exitReasons(answer.failure_reasons) },
  };
}

export function submittedProposal(text) {
  const line = reviewLine(text);
  if (line === null) return null;
  const answer = readProposal(line, false);
  if (answer.operation === reviewedPlanOperation) return reviewedPlan(answer);
  if (answer.operation !== reviewedScriptOperation) {
    throw new ProposalError('An exact-script submission must use operation reviewed-script.');
  }
  return reviewedProposal(answer);
}

export function proposal(text, operations) {
  const answer = readProposal(text, true);
  const summary = reviewText(answer.summary, 220);
  if (answer.operation === null) return { summary: summary || 'Root could not prepare an exact script for review.' };
  if (answer.operation === reviewedScriptOperation) return reviewedProposal(answer);
  if (answer.operation === reviewedPlanOperation) return reviewedPlan(answer);
  if (typeof answer.operation !== 'string' || !Object.hasOwn(operations, answer.operation)) {
    throw new ProposalError('The proposal names an operation Root does not recognize.');
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
  verification_failed: 'The operation ran but its post-change check failed.',
  rolled_back: 'Verification failed and the original file was restored.',
  file_changed: 'The file no longer matches the reviewed version.',
  precondition_failed: 'The file or its metadata could not be checked safely.',
  recovery_required: 'Verification and automatic file recovery failed. Check the host and private backup.',
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

export function sourceTurn(content, config, event, coordinator) {
  const turn = content?.[agentTurnKey];
  if (content?.['m.relates_to']?.rel_type !== 'm.thread' || typeof turn?.request !== 'string' ||
      !eventId.test(turn.request) || !Number.isInteger(turn.number) || turn.number < 1 || turn.number >= replyLimit) return null;
  const base = { request: turn.request, number: turn.number };
  const managed = turn.managed;
  if (!managed) return base;
  const manager = coordinator ?? '@manager:' + config.root.split(':').slice(1).join(':');
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
        id: entry.id, type: entry.operation, executed_on: process.env.ROOT_HOST ?? 'deployment-host', action: entry.action,
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
