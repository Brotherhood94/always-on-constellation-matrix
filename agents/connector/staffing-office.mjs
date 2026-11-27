import { mentionMessage } from './matrix-format.mjs';

export const staffingReceiptKey = 'org.matrixconstellation.staffing.receipt';
const turnKey = 'net.matrix_agents.turn';
const eventId = /^\$[^\s/#?%]{1,254}$/u;
const agentKey = /^[a-z0-9][a-z0-9_-]{0,35}$/u;

export const staffingGuidance = 'For an explicit request to hire a new agent, use the hiring-manager staffing handoff. ' +
  'Add "staffing":{"action":"hire","key":"requested-agent-id","brief":"requested duties","web":true} to the HANDOFF object. ' +
  'Keep the requested ID, use a suitable unused key when none was named, and set web to true only when public web research is required. ' +
  'Include any proposed first task in the brief. Hiring Manager posts the private approval and reports its actual status.';

export function staffingRequest(value) {
  if (value?.action !== 'hire' || typeof value.key !== 'string' || !agentKey.test(value.key) ||
      typeof value.brief !== 'string' || !value.brief.trim() || value.brief.length > 1200 ||
      /[\p{Cc}\u202a-\u202e\u2066-\u2069]/u.test(value.brief.replace(/[\n\t]/gu, '')) ||
      typeof value.web !== 'boolean') return null;
  return { action: 'hire', key: value.key, brief: value.brief.trim(), web: value.web };
}

function mentions(content, user) {
  const value = content?.['m.mentions'];
  return !value?.room && Array.isArray(value?.user_ids) && value.user_ids.length === 1 && value.user_ids[0] === user;
}

export async function verifyStaffingHandoff({ read, request, handoff, thread, manager, hiring, owner, planner }) {
  if (![request, handoff, thread].every(id => eventId.test(id ?? '')) || !manager || !hiring) return null;
  const dispatch = await read(handoff);
  const turn = dispatch?.content?.[turnKey];
  const relation = dispatch?.content?.['m.relates_to'];
  if (dispatch?.sender !== manager || turn?.request !== request || turn.number !== 1 ||
      turn.managed?.version !== 2 || turn.managed.manager !== manager || turn.managed.target !== hiring ||
      turn.handoff?.capability !== 'staffing' || !/^(task|thread):/u.test(turn.handoff.resource ?? '') ||
      !mentions(dispatch.content, hiring) || relation?.rel_type !== 'm.thread' || relation.event_id !== thread ||
      relation['m.in_reply_to']?.event_id !== request) return null;
  const operation = staffingRequest(turn.staffing);
  if (!operation) return null;
  const original = await read(request);
  const originalRelation = original?.content?.['m.relates_to'];
  if (!original?.sender || ![owner, planner].includes(original.sender) || !mentions(original.content, manager) ||
      (request === thread ? originalRelation : originalRelation?.rel_type !== 'm.thread' || originalRelation.event_id !== thread) ||
      (original.sender === planner && typeof original.content['net.matrix_agents.dot_plan']?.task_id !== 'string')) return null;
  return { operation, requester: original.sender };
}

const messages = {
  preparing: key => `Hiring Manager is preparing the private proposal for ${key}.`,
  awaiting_approval: key => `The proposal for ${key} is awaiting your vote in Agents → People → Hiring Manager.`,
  provisioning: key => `The hire for ${key} was approved. Registration is in progress.`,
  provider_required: key => `Registered ${key}. Complete Amplifier provider setup on the VPS with ./constellation_matrix agent configure ${key}.`,
  awaiting_readiness: key => `Provider setup is recorded for ${key}. Waiting for the agent to become available in Office.`,
  awaiting_web: key => `${key} is available in Office. Ask Scouter for a read-only public web tool and approve its separate grant. The hiring request is waiting for the tool verification.`,
  ready: key => `${key} is registered, available in Office, and has passed the required setup checks.`,
  declined: key => `The operator declined the hire for ${key}.`,
  expired: key => `The approval for ${key} expired. Start a new hiring request to try again.`,
  rejected: key => `The roster service could not register ${key}. Check the worker ID and roster status before retrying.`,
  conflict: key => `There is already a registration or pending proposal for ${key}. Check the roster or the Hiring Manager chat.`,
  unavailable: key => `Roster management is unavailable for ${key}. Check the Hiring Manager service before retrying.`,
};

export function staffingReceipt(value) {
  if (!eventId.test(value?.request ?? '') || !eventId.test(value.handoff ?? '') ||
      typeof value.agent !== 'string' || !agentKey.test(value.agent) || !Object.hasOwn(messages, value.status)) return null;
  return { request: value.request, handoff: value.handoff, agent: value.agent, status: value.status };
}

export function staffingOutcome(receipt, event) {
  const { status, agent } = receipt;
  if (status === 'ready') return { status: 'completed', result: messages.ready(agent),
    verification: 'Hiring Manager verified the registered agent and its Office readiness.', evidence: [event] };
  if (['declined', 'expired', 'rejected', 'conflict', 'unavailable'].includes(status)) {
    return { status: 'blocked', blocker: messages[status](agent), unblocker: 'The operator',
      action: 'Review the Hiring Manager chat before starting another hiring request.' };
  }
  return null;
}

export function staffingNotice(receipt, thread, requester) {
  return {
    ...mentionMessage(messages[receipt.status](receipt.agent), requester),
    [staffingReceiptKey]: receipt,
    'm.relates_to': { rel_type: 'm.thread', event_id: thread, is_falling_back: true,
      'm.in_reply_to': { event_id: receipt.handoff } },
  };
}
