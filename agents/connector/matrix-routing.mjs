import { parseDocument } from 'htmlparser2';
import { mentionMessage, outcomeReplyText } from './matrix-format.mjs';
import { acceptedManaged, managedReply, replyLimit } from './managed-task.mjs';
import { acceptsContact } from './agent-catalog.mjs';
import { applyDispatch } from './capability-dispatch.mjs';
import { explicitTelegramRequest, telegramIntent, telegramMention, telegramTaskPayload } from './telegram-intent.mjs';

export { mentionMessage, replyLimit };

export const turnKey = 'net.matrix_agents.turn';
export const dotPlanKey = 'net.matrix_agents.dot_plan';
export const directReplyKey = 'net.matrix_agents.direct_reply';
export const taskOutcomeKey = 'net.matrix_agents.task_outcome';
export const attachmentKey = 'net.matrix_agents.attachments';
export const mediaTypes = new Set(['m.file', 'm.image', 'm.audio', 'm.video']);

export function telegramOfficeAssignment(event, { owner, planner, userId, name }) {
  const sender = event?.sender;
  if (!sender || (sender !== owner && sender !== planner)) return null;
  let text = telegramMention(event.content, userId, name);
  if (text === null) return null;
  if (sender !== owner) {
    const { content } = event;
    const plan = content[dotPlanKey];
    if (content['m.relates_to'] || typeof plan?.task_id !== 'string' || !/^[a-z0-9_-]{8,80}$/iu.test(plan.task_id)) return null;
    if (Object.hasOwn(plan, 'telegram')) {
      const telegram = telegramTaskPayload(plan.telegram);
      return telegram && { sender, telegram };
    }
    const start = text.indexOf('\n\n');
    if (start === -1) return null;
    text = text.slice(start + 2).trim();
    if (/\r|\n/u.test(text)) return null;
  }
  const intent = telegramIntent(text, { office: true });
  if (!intent) return explicitTelegramRequest(text) ? { sender, text } : null;
  // Use forms understood by already running Telegram connectors.
  if (intent.type === 'direct' && /^@[a-z0-9_]{4,32}$/iu.test(intent.target)) {
    if (intent.action === 'open') text = 'Telegram: open ' + intent.target;
    if (intent.action === 'send' && !/["\r\n]/u.test(intent.text)) text = `Telegram: send a private message to ${intent.target} saying "${intent.text}"`;
  }
  return { sender, text };
}
const replyControl = /(?:^|\n)(HANDOFF|HANDOFF_ROUTE|TASK_OUTCOME|ATTACH_FILES|CAPABILITY_GAP|OFFICE_NEED):[ \t]+([^\r\n]*)$/;
const directRequests = [
  /(?<!\bnon )\b(?:rispondimi\s+direttamente|rispondi\s+(?:a\s+me\s+|tu\s+)?direttamente(?!\s+(?:a\s+(?!me\b)|al\b|alla\b)))\b/iu,
  /(?<!\bdo not )(?<!\bdon['’]t )(?<!\bnever )\b(?:reply|respond|answer)\s+(?:(?:to\s+)?me\s+directly|directly(?!\s+to\s+(?!me\b)))\b/iu,
];
const statusRequests = [
  /\b(?:a che punto|come procede|come sta andando|stato attuale|current status|latest status|status update|mi aggiorni (?:su|sul|sullo|sull['’])|how (?:is|far) .* (?:coming along|progressing))\b/iu,
  /\b(?:(?:qual è|qual e|mi dici|dimmi|verifica|riporta|controlla|aggiornami sul)\s+(?:il |lo |l['’])?(?:stato|avanzamento)|(?:check|report|give|tell|show)\s+[^.!?]{0,30}\b(?:status|progress)\b)/iu,
  /\b(?:what(?: is|['’]s)|where(?: is|['’]s)|how (?:far|much)|qual è|qual e|quali sono|dov['’]è|dove (?:si trova|trovo)|(?:mi dai|dammi|mandami|send me|share)\s+(?:il |the )?(?:link|url))(?![\p{L}\p{N}_])/iu,
];
export const agentNames = { analyst: 'Analyst', reviewer: 'Reviewer', coder: 'Coder', manager: 'Manager' };
const handoffRequest = /\b(?:handoff to|hand (?:it|this) off|passa (?:il lavoro|la parola))\b/iu;

export function team(server) {
  return Object.fromEntries(Object.entries(agentNames).map(([username, name]) => [`@${username}:${server}`, name]));
}

function matrixUser(target) {
  if (typeof target !== 'string') return null;
  try {
    const url = new URL(target);
    if (url.protocol === 'https:' && url.hostname === 'matrix.to' && url.hash.startsWith('#/')) {
      return decodeURIComponent(url.hash.slice(2).split(/[/?]/, 1)[0]);
    }
    if (url.protocol === 'matrix:' && url.pathname.startsWith('u/')) {
      return '@' + decodeURIComponent(url.pathname.slice(2));
    }
  } catch {
    return null;
  }
  return null;
}

function getMentions(nodes, result = new Set()) {
  for (const node of nodes) {
    if (node.name === 'mx-reply' || node.name === 'blockquote') continue;
    if (node.name === 'a') {
      const user = matrixUser(node.attribs.href);
      if (user) result.add(user);
    }
    if (node.children) getMentions(node.children, result);
  }
  return result;
}

function newMessageText(nodes) {
  return nodes.map(node => {
    if (node.type === 'text') return node.data;
    if (!node.children || ['mx-reply', 'blockquote', 'code', 'pre', 'a'].includes(node.name)) return ' ';
    return newMessageText(node.children);
  }).join(' ');
}

function directAnswerRequested(markup, userId, agents, displayNames) {
  const text = newMessageText(markup).replace(/\s+/g, ' ');
  if (directRequests.some(pattern => pattern.test(text))) return true;
  if (handoffRequest.test(text) || !statusRequests.some(pattern => pattern.test(text))) return false;
  const peers = new Set(agents.filter(id => id !== userId).flatMap(id => [id.slice(1).split(':', 1)[0], displayNames[id]]).filter(Boolean));
  if (!peers.size) return true;
  const collaborationRequest = new RegExp(
    String.raw`\b(?:ask|tag|involve|delegate|chiedi|tagga|coinvolgi|delega|fai verificare)\b[^.!?]{0,80}\b(?:${[...peers].map(RegExp.escape).join('|')})(?![\p{L}\p{N}_-])`,
    'iu',
  );
  return !collaborationRequest.test(text);
}

export function acceptedMessage(event, { roomId, owner, userId, agents, activatedAt, planner, displayNames = {}, routingPolicy, acceptAttachments = true }) {
  if (event?.room_id !== roomId || event.sender === userId ||
      (event.sender !== owner && (!planner || event.sender !== planner) && !agents.includes(event.sender)) ||
      typeof event.event_id !== 'string' || !Number.isFinite(event.origin_server_ts) ||
      event.origin_server_ts < activatedAt) return null;
  const content = event.content;
  const hasFile = mediaTypes.has(content?.msgtype);
  if (hasFile && !acceptAttachments) return null;
  if ((!hasFile && content?.msgtype !== 'm.text') || content[directReplyKey] === true || event.type !== 'm.room.message' ||
      typeof content.body !== 'string' || content.body.length > 20000) return null;
  const formatted = content.formatted_body;
  if ((!hasFile && typeof formatted !== 'string') || (typeof formatted === 'string' && formatted.length > 100000)) return null;
  const relation = content['m.relates_to'];
  if (relation?.rel_type && relation.rel_type !== 'm.thread') return null;
  const planned = Boolean(planner && event.sender === planner);
  if (planned && (relation || typeof content[dotPlanKey]?.task_id !== 'string')) return null;
  const mentions = content['m.mentions']?.user_ids;
  if (!Array.isArray(mentions)) return null;
  const markup = typeof formatted === 'string' ? parseDocument(formatted).children : [];
  const mentionedAgents = [...new Set(mentions)].filter(id => agents.includes(id));
  if (mentionedAgents.length !== 1 || mentionedAgents[0] !== userId ||
      (typeof formatted === 'string' && !getMentions(markup).has(userId))) return null;

  const direct = (event.sender === owner || planned) && directAnswerRequested(markup, userId, agents, displayNames);
  const body = hasFile && (!content.filename || content.filename === content.body)
    ? 'Read the attached file and report what it contains.' : content.body.replace(/^(?:>[^\n]*\n)+\n/, '').trim();
  if (!body) return null;
  const thread = relation?.rel_type === 'm.thread' ? relation.event_id : event.event_id;
  if (typeof thread !== 'string' || !thread.startsWith('$')) return null;
  let request = event.event_id;
  let number = 1;
  const delegated = event.sender !== owner && !planned;
  if (delegated) {
    const turn = content[turnKey];
    if (relation?.rel_type !== 'm.thread' || typeof turn?.request !== 'string' ||
        !turn.request.startsWith('$') || !Number.isInteger(turn.number) ||
        turn.number < 1 || turn.number >= replyLimit) return null;
    if (!acceptsContact(routingPolicy, event, userId)) return null;
    request = turn.request;
    number = turn.number + 1;
  }
  const managed = acceptedManaged(event, displayNames, userId, delegated, direct, routingPolicy);
  if (managed === null) return null;
  const refs = new Map();
  if (hasFile) refs.set(event.event_id, true);
  if (acceptAttachments && event.sender === owner) {
    for (const id of [relation?.['m.in_reply_to']?.event_id, relation?.rel_type === 'm.thread' && relation.event_id]) {
      if (validEventId(id) && !refs.has(id)) refs.set(id, false);
    }
  } else if (acceptAttachments && number > 1 && Array.isArray(content[attachmentKey])) {
    for (const id of content[attachmentKey]) {
      if (validEventId(id)) refs.set(id, true);
    }
  }
  return { id: event.event_id, thread, body, request, number, delegator: number === 1 ? null : event.sender,
    ...(managed && { managed }),
    ...(direct && { replyTo: event.sender }),
    ...(refs.size && { attachmentRefs: [...refs].map(([id, required]) => ({ id, required })) }),
  };
}

function validEventId(id) {
  return typeof id === 'string' && id.length > 1 && id.length <= 255 && id.startsWith('$');
}

export function validatedOutcome(value) {
  const { status } = value ?? {};
  const fields = status === 'completed' ? ['result', 'verification'] :
    status === 'blocked' ? ['blocker', 'unblocker', 'action'] : [];
  if (!fields.length || !fields.every(field => typeof value[field] === 'string' && value[field].trim() && value[field].length <= 1200)) return null;
  const evidence = status === 'completed' && Array.isArray(value.evidence)
    ? [...new Set(value.evidence.filter(validEventId))].slice(0, 8) : [];
  return { status, ...Object.fromEntries(fields.map(field => [field, value[field].trim()])),
    ...(evidence.length && { evidence }) };
}

function replyParts(reply) {
  let body = reply.trimEnd();
  const controls = new Map();
  let match;
  while ((match = body.match(replyControl))) {
    if (!controls.has(match[1])) controls.set(match[1], match[2].trim());
    body = body.slice(0, match.index).trimEnd();
  }
  let outcome = null;
  if (controls.has('TASK_OUTCOME')) {
    try {
      outcome = validatedOutcome(JSON.parse(controls.get('TASK_OUTCOME')));
    } catch {
      // Malformed metadata is an ordinary reply without a terminal outcome.
    }
  }
  let paths = [];
  let issue;
  if (controls.has('ATTACH_FILES')) {
    try {
      const requested = JSON.parse(controls.get('ATTACH_FILES'));
      if (!Array.isArray(requested) || !requested.every(path => typeof path === 'string' && path.length > 0 && path.length <= 1024)) {
        throw new Error('Unexpected attachment paths');
      }
      paths = [...new Set(requested)];
    } catch {
      issue = 'Attachment refused: the agent did not provide valid workspace paths.';
    }
  }
  let route;
  if (controls.has('HANDOFF_ROUTE')) {
    try {
      route = JSON.parse(controls.get('HANDOFF_ROUTE'));
    } catch {
      route = null;
    }
  }
  return { body, outcome, paths, issue, route, attachmentLine: controls.get('ATTACH_FILES'), handoff: controls.get('HANDOFF') };
}

export const declaredOutcome = reply => replyParts(reply).outcome;

export function requestedAttachments(reply) {
  const { paths, issue } = replyParts(reply);
  return { paths, issue };
}

export function returnDelegatedReview(event, body, delegator) {
  const number = event.number ?? 1;
  if (!delegator || number <= 1 || number >= replyLimit) return body;
  const review = replyParts(body);
  if (review.outcome?.status === 'blocked' && !review.handoff) return body;
  return [review.body, review.attachmentLine ? `ATTACH_FILES: ${review.attachmentLine}` : '', `HANDOFF: ${delegator.name}`]
    .filter(Boolean).join('\n\n');
}

export function threadRelation(event) {
  return { rel_type: 'm.thread', event_id: event.thread, is_falling_back: true, 'm.in_reply_to': { event_id: event.id } };
}

export function threadReply(event, reply, peers = [], requesterName, outcomeRecipients = [], delivery = {}, decision) {
  const parts = applyDispatch(replyParts(reply), decision);
  const { body, outcome: declared, handoff } = parts;
  const number = event.number ?? 1;
  if (event.managed && !event.replyTo) {
    const result = managedReply(event, parts, peers, delivery.issues);
    const message = outcomeReplyText(result.body, result.outcome, delivery.issues);
    return {
      ...mentionMessage(message.plain, result.outcome ? outcomeRecipients : result.target, message.rich),
      [turnKey]: { request: event.request, number, ...(result.managed && { managed: result.managed }),
        ...(result.target && (decision?.target?.id === result.target.id && decision.requirement
          ? { handoff: decision.requirement, ...(decision.staffing && { staffing: decision.staffing }) }
          : result.target.id === event.managed.manager ? { returning: true } : {})) },
      ...(result.outcome && { [taskOutcomeKey]: result.outcome }),
      ...(delivery.ids?.length && { [attachmentKey]: delivery.ids }),
      'm.relates_to': threadRelation(event),
    };
  }
  const peer = decision?.returnTo || parts.target || (handoff && peers.find(candidate => candidate.name === handoff));
  let outcome = event.replyTo || peer || handoff ? null : declared;
  if (!event.replyTo && peer && number === replyLimit) {
    outcome = {
      status: 'blocked',
      blocker: `The ${replyLimit}-reply limit stopped the handoff to ${peer.name}.`,
      unblocker: 'The Matrix owner',
      action: `Mention ${peer.name} in this thread with the remaining work to start a new request.`,
    };
  }
  if (!event.replyTo && delivery.issues?.length && outcome?.status !== 'blocked') {
    outcome = { status: 'blocked', blocker: delivery.issues[0], unblocker: 'The requester',
      action: 'Provide a smaller or readable file, then mention the agent again.' };
  }
  const target = event.replyTo ? { id: event.replyTo, name: requesterName || event.replyTo.slice(1).split(':', 1)[0] } :
    outcome ? outcomeRecipients : number < replyLimit ? peer : null;
  const returnedStep = decision?.returnTo && declared ? (declared.status === 'blocked' ?
    ['Delegated step blocked', declared.blocker, declared.action] : ['Delegated result', declared.result, declared.verification]) : [];
  const message = outcomeReplyText([body, ...returnedStep].filter(Boolean).join('\n'), outcome, delivery.issues);
  return {
    ...mentionMessage(message.plain, target, message.rich),
    [turnKey]: { request: event.request ?? event.id, number, ...(peer && decision?.requirement && { handoff: decision.requirement }),
      ...(peer && decision?.returnTo && {returning:true}) },
    ...(event.replyTo && { [directReplyKey]: true }),
    ...(outcome && { [taskOutcomeKey]: outcome }),
    ...(delivery.ids?.length && { [attachmentKey]: delivery.ids }),
    'm.relates_to': threadRelation(event),
  };
}
