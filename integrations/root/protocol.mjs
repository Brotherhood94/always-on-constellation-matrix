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
export const maintenanceReleaseOperation = 'maintenance-release';

export function maintenanceProposal(result) {
  const { release, diff, tests } = result;
  if (!release || !/^[a-f0-9]{40,64}$/u.test(release.base ?? '') || !fingerprint.test(release.revision ?? '') ||
      typeof diff !== 'string' || Buffer.byteLength(diff) > 5000 || digest(diff) !== release.diff_sha256 ||
      typeof tests !== 'string' || tests.length > 200 || !Array.isArray(release.services) ||
      release.services.some(key => !/^[a-z0-9][a-z0-9_-]{0,35}$/u.test(key)) || typeof release.dashboard !== 'boolean') {
    throw new ProposalError('The host did not return a tested maintenance release.');
  }
  const command = JSON.stringify(release);
  return { operation: maintenanceReleaseOperation, summary: 'Apply the tested Constellation repository change.',
    command, hash: digest(command),
    details: { verification: tests, recovery: 'Restore the previous release if a health check fails.', diff } };
}
export const maximumScriptBytes = 4000;
const reviewPrefix = 'ROOT_REVIEW:';
const serviceSelector = /^[a-z0-9][a-z0-9._@-]{0,79}$/u;
const maximumReviewRequestBytes = 12000;

const pollResponse = 'org.matrix.msc3381.poll.response';
export const pollChoice = (task, action) => digest(JSON.stringify([task.id, task.hash, task.proposal_event, action]));

export function requestText(event, root, maximumBytes = 1800, direct = false) {
  const { type, content } = event;
  const mentions = content?.['m.mentions'];
  if (type !== 'm.room.message' || content?.msgtype !== 'm.text' || typeof content.body !== 'string' ||
      content['m.new_content'] || mentions?.room ||
      (!direct && (mentions?.user_ids?.length !== 1 || mentions.user_ids[0] !== root))) return null;
  const relation = content['m.relates_to'];
  if (relation && (relation.rel_type !== 'm.thread' || !eventId.test(relation.event_id))) return null;
  const text = content.body.replace(/^(?:>[^\n]*\n)+\n/u, '')
    .replace(/^\s*@?(?:the-architect|TheArchitect|root)(?::[a-z0-9.-]+)?[,:\s]+/iu, '').trim();
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

export function repositoryIntent(text) {
  try {
    const value = readProposal(text, true);
    return Object.keys(value).length === 1 && value.operation === 'prepare-repository-change';
  } catch (error) {
    if (!(error instanceof ProposalError)) throw error;
    return false;
  }
}

function reviewLine(text) {
  const lastLine = text.slice(text.lastIndexOf('\n') + 1);
  if (lastLine.startsWith('VPS_REVIEW:')) return lastLine.slice('VPS_REVIEW:'.length);
  return lastLine.startsWith(reviewPrefix) ? lastLine.slice(reviewPrefix.length) : null;
}

function reviewedPlan(answer) {
  const { plan } = answer;
  if (!plan || !['shell', 'inspect-file', 'replace-file', 'inspect-services', 'restart-service'].includes(plan.kind)) {
    throw new ProposalError('Specify a shell, inspect-file, replace-file or inspect-services maintenance plan.');
  }
  if (plan.kind === 'inspect-services') return serviceInspection(plan.target);
  if (plan.kind === 'restart-service') {
    const inspection = serviceInspection(plan.target);
    const command = JSON.stringify({ kind: plan.kind, target: plan.target });
    return { ...inspection, command, hash: digest(command), summary: `Restart ${plan.target} in this deployment.`,
      details: { impact: 'Briefly interrupts one running service.', verification: 'Waits for Docker to confirm readiness.',
        recovery: 'Inspect the service in Control Room if readiness fails.' } };
  }
  const exact = { kind: plan.kind };
  if (['inspect-file', 'replace-file'].includes(plan.kind)) {
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

function serviceInspection(target) {
  if (typeof target !== 'string' || !serviceSelector.test(target)) {
    throw new ProposalError('Specify one service ID or all for a service inspection.');
  }
  const overview = target === 'all';
  const command = JSON.stringify({ kind: 'inspect-services', target });
  return {
    operation: reviewedPlanOperation, command, hash: digest(command),
    summary: overview ? 'Check Constellation service health on the VPS.' : `Inspect ${target} on the VPS.`,
    details: {
      impact: overview ? 'Reads local Docker Compose and systemd service status.' :
        'Reads status and counts recognized signals in recent logs for matching local services.',
      verification: 'Check the private receipt for service state and any unavailable components.',
      recovery: 'Request a separate review for any change to the host.',
    },
  };
}

export function serviceDiagnosticProposal(text) {
  const request = text.trim().toLowerCase();
  const match = /^(?:diagnostica|diagnosi|diagnose|troubleshoot|diagnostics)\s+(?:(?:il servizio|service)\s+)?([a-z0-9][a-z0-9._@-]{0,79}|context intelligence)[.!?]?$/u.exec(request);
  if (!match) return null;
  const target = match[1].replace(/\.$/u, '').replace(' ', '-');
  return serviceInspection(['services', 'servizi'].includes(target) ? 'all' : target);
}

export function twinRequest(text) {
  if (/^(?:twin|(?:crea|avvia|create|start) (?:un |a )?(?:digital )?twin)(?:\b.*)?$/iu.test(text) &&
      !/^twin (?:models|create)\b/iu.test(text)) return { action: 'choices' };
  const models = /^twin models ([a-f0-9]{24})$/u.exec(text);
  if (models) return { action: 'models', account: models[1] };
  const create = /^twin create ([a-f0-9]{24}) ([a-zA-Z0-9_.-]{3,80}) (none|minimal|low|medium|high|xhigh) ([A-Za-z0-9_.:,-]+)(?: (\d{1,3}))?$/u.exec(text);
  if (!create) return null;
  const parts = create[4].split(',').map(part => part.split(':'));
  const agents = parts.map(([agent]) => agent);
  const overrides = Object.fromEntries(parts.filter(part => part.length === 3)
    .map(([agent, model, effort]) => [agent, { model, effort }]));
  const minutes = Number(create[5] ?? '120');
  if (agents.length > 8 || agents.some(agent => !/^[a-z0-9][a-z0-9_-]{0,35}$/u.test(agent)) ||
      new Set(agents).size !== agents.length || minutes < 15 || minutes > 360 ||
      parts.some(part => part.length !== 1 && (part.length !== 3 || !/^[A-Za-z0-9][A-Za-z0-9._-]{2,79}$/u.test(part[1]) ||
        !['none', 'minimal', 'low', 'medium', 'high', 'xhigh'].includes(part[2])))) {
    throw new ProposalError('Choose one to eight agents and a duration from 15 to 360 minutes.');
  }
  return { action: 'create', account: create[1], model: create[2], effort: create[3], agents, minutes, overrides };
}

export function twinProposal({ action, ...selection }, revision) {
  if (!fingerprint.test(revision)) throw new ProposalError('The deployment snapshot is unavailable.');
  const command = JSON.stringify({ kind: 'twin-create', ...selection, revision });
  return { operation: reviewedPlanOperation, summary: 'Start a private digital twin.', command, hash: digest(command),
    details: { impact: 'Starts a separate Docker workspace and uses the selected provider connection.',
      verification: 'Probes the selected Amplifier instances and notifies Control Room when ready.',
      recovery: 'Stop the twin to revoke grants and remove its temporary workspace.' } };
}

export function reviewBody(task) {
  if (task.operation === maintenanceReleaseOperation) {
    const release = JSON.parse(task.command);
    const { diff } = JSON.parse(task.review_details);
    return [
      'Base ' + release.base, 'Candidate ' + release.revision,
      'Changes: ' + [...release.services, ...(release.dashboard ? ['dashboard'] : [])].join(', '),
      'Diff SHA-256 ' + release.diff_sha256, '', diff,
    ].join('\n');
  }
  if (task.operation !== reviewedPlanOperation) return task.command;
  const plan = JSON.parse(task.command);
  if (plan.kind === 'shell') return 'Apply\n' + plan.script + '\n\nVerify\n' + plan.verify;
  if (plan.kind === 'telegram-diagnostics') return [
    'List the Telegram bridge, guide, connector and engine containers for this deployment.',
    'Check the local status and health. Inspect up to 100 recent log lines per service from the last 30 minutes.',
    'Return only service status and counts for recognized diagnostic signals. No messages are sent to Telegram.',
  ].join('\n');
  if (plan.kind === 'inspect-services') return [
    'Target: ' + plan.target,
    'Read the state of matching local Constellation Docker Compose services and systemd units.',
    plan.target === 'all' ? 'Return a bounded status overview.' :
      'Count known signals in up to 100 log lines per service from the last 30 minutes, for up to six matching instances.',
    'Return only service states and signal counts.',
  ].join('\n');
  if (plan.kind === 'twin-create') return `Revision ${plan.revision}\nAgents ${plan.agents.join(', ')}\n` +
    `Account ${plan.account}\nModel ${plan.model} · Reasoning ${plan.effort}\nDuration ${plan.minutes} minutes` +
    Object.entries(plan.overrides ?? {}).map(([agent, profile]) => `\n${agent} · ${profile.model} · ${profile.effort}`).join('');
  if (plan.kind === 'restart-service') return `Restart the running Compose service ${plan.target} and verify its state.`;
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
  if (task.operation === maintenanceReleaseOperation) {
    const cause = {
      rolled_back: 'The broker restored and verified the previous release.',
      file_changed: 'The active revision changed after review.',
      precondition_failed: 'The broker could not confirm the workspace or connector preconditions.',
      recovery_required: 'Check the affected services and use maintenance rollback with the operation ID.',
    }[task.outcome];
    if (cause) return cause;
  }
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
      ...(stage === 'finished' && { timing: { id: entry.id, kind: 'control-operation', outcome: entry.outcome,
        total_ms: Math.max(0, entry.finished_at - entry.time),
        ...(entry.decided && entry.proposed_at && { approval_ms: Math.max(0, entry.decided - entry.proposed_at) }),
        ...(entry.execution_ms !== null && entry.execution_ms !== undefined && { work_ms: Number(entry.execution_ms) }),
      }}),
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
