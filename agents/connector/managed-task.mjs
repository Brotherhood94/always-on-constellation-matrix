import { replyLimit } from './office-policy.mjs';

export { replyLimit };

const validEventId = id => typeof id === 'string' && id.startsWith('$') && id.length > 1 && id.length <= 255;
const managerId = names => Object.keys(names).find(id => names[id] === 'Manager');

function validPath(path, start, manager, names) {
  return Array.isArray(path) && path.length > 0 && path.length <= replyLimit - start - 1 &&
    path.every((id, index) => typeof id === 'string' && Object.hasOwn(names, id) &&
      id !== manager && id !== path[index - 1] &&
      (names[id] !== 'Reviewer' || (path[index + 1] ?? manager) === (path[index - 1] ?? manager)));
}

function handoff(content, sender, id, names) {
  if (content?.msgtype !== 'm.text' || typeof content.body !== 'string') return null;
  const turn = content?.['net.matrix_agents.turn'];
  const route = turn?.managed;
  const manager = managerId(names);
  if (!manager || route?.manager !== manager || !Number.isInteger(route.start) ||
      route.start < 1 || !validPath(route.path, route.start, manager, names)) return null;
  const index = turn.number - route.start;
  if (!Number.isInteger(turn.number) || index < 0 || index > route.path.length ||
      sender !== (index === 0 ? manager : route.path[index - 1])) return null;
  const relation = content['m.relates_to'];
  const previous = relation?.['m.in_reply_to']?.event_id;
  if (relation?.rel_type !== 'm.thread' || !validEventId(previous) || !validEventId(turn.request)) return null;
  const blocked = route.blocked === true;
  if ((index === 0 && (blocked || route.dispatch !== undefined)) ||
      (index > 0 && !validEventId(route.dispatch))) return null;
  const target = blocked ? manager : route.path[index] ?? manager;
  const mentions = content['m.mentions']?.user_ids;
  if (!Array.isArray(mentions) || !mentions.includes(target)) return null;
  return { target, request: turn.request, number: turn.number, thread: relation.event_id,
    manager, start: route.start, path: route.path, dispatch: index === 0 ? id : route.dispatch,
    previous, blocked };
}

function dynamicHandoff(event, manager, known, activators) {
  const turn = event?.content?.['net.matrix_agents.turn'];
  const route = turn?.managed;
  const relation = event?.content?.['m.relates_to'];
  const previous = relation?.['m.in_reply_to']?.event_id;
  if (route?.version !== 2 || !manager || route.manager !== manager ||
      !Object.hasOwn(known, event.sender) || !Object.hasOwn(known, route.target) || route.target === event.sender ||
      relation?.rel_type !== 'm.thread' || !validEventId(relation.event_id) || !validEventId(turn.request) ||
      !validEventId(event.event_id) || !validEventId(previous) || !Number.isInteger(turn.number) ||
      turn.number < 1 || turn.number >= replyLimit || (turn.number === replyLimit - 1 && route.target !== manager)) return null;
  const mentions = event.content['m.mentions']?.user_ids;
  if (!Array.isArray(mentions) || !mentions.includes(route.target) ||
      new Set(mentions.filter(id => Object.hasOwn(known, id))).size !== 1) return null;
  if (turn.number === 1 ? (event.sender !== manager || previous !== turn.request || route.dispatch !== undefined)
    : !validEventId(route.dispatch)) return null;
  return { version: 2, manager, target: route.target, number: turn.number, request: turn.request, thread: relation.event_id,
    dispatch: route.dispatch ?? event.event_id, previous, activators };
}

export function acceptedManaged(event, names, userId, delegated, direct, policy = {}) {
  const manager = managerId(names);
  const coordinator = policy.coordinator ?? manager;
  if (delegated && event.content['net.matrix_agents.turn']?.managed?.version === 2) {
    const next = dynamicHandoff(event, coordinator, names, policy.activators);
    return next?.target === userId ? next : null;
  }
  if (!delegated && !direct && coordinator === userId && policy.dynamic) return { manager: coordinator, version: 2 };
  if (!delegated) return !direct && userId === manager ? { manager } : undefined;
  if (!event.content['net.matrix_agents.turn']?.managed && userId !== manager && event.sender !== manager) return;
  const managed = handoff(event.content, event.sender, event.event_id, names);
  return managed?.target === userId ? managed : null;
}

// Read the verified Matrix events for this dispatch, including the original
// request. No worker can extend the route declared by the Manager.
export async function managedContext(task, names, readEvent) {
  if (!task.managed?.dispatch) return task;
  if (task.managed.version === 2) {
    const route = task.managed;
    const history = [];
    let eventId = task.id;
    let target = route.target;
    for (let number = route.number; number >= 1; number -= 1) {
      const event = await readEvent(eventId);
      const step = event && dynamicHandoff({ ...event, event_id: eventId }, route.manager, names);
      if (!step || step.target !== target || step.dispatch !== route.dispatch || step.thread !== task.thread ||
          step.request !== task.request || step.number !== number) return null;
      history.unshift(`${names[event.sender]}:\n${event.content.body.slice(0, 4500)}`);
      target = event.sender;
      eventId = step.previous;
    }
    const initial = await readEvent(eventId);
    if (eventId !== task.request || !route.activators?.includes(initial?.sender) ||
        !initial?.content?.['m.mentions']?.user_ids?.includes(route.manager) || typeof initial.content.body !== 'string') return null;
    return { ...task, body: ['Original request:', initial.content.body.slice(0, 10000), ...history].join('\n\n') };
  }
  const route = task.managed;
  const history = [];
  let previous = route.previous;
  for (let number = route.number - 1; number >= route.start; number -= 1) {
    const event = await readEvent(previous);
    const step = event && handoff(event.content, event.sender, previous, names);
    const next = route.path[number - route.start];
    if (!step || step.request !== task.request || step.thread !== task.thread ||
        step.number !== number || step.start !== route.start || step.dispatch !== route.dispatch ||
        step.blocked || step.target !== next || JSON.stringify(step.path) !== JSON.stringify(route.path)) return null;
    history.unshift(`${names[event.sender]}:\n${event.content.body.slice(0, 6000)}`);
    previous = step.previous;
  }
  const request = await readEvent(task.request);
  if (!request || typeof request.content?.body !== 'string') return null;
  const body = ['Original request:', request.content.body.slice(0, 12000),
    ...history, `${names[task.delegator]} (current handoff):\n${task.body}`].join('\n\n');
  return { ...task, body };
}

export function managedGuidance(event, name, names, userId) {
  if (!event.managed || event.replyTo) return null;
  const { manager, start, path } = event.managed;
  if (event.managed.version === 2) {
    const position = `This is reply ${event.number} of ${replyLimit}. `;
    return position + (userId === manager
      ? 'Own the assignment, delegate only necessary work, and verify evidence before closing it. Only you report the final outcome. ' +
        'On a delegated blocker, investigate the cause, preserve verified work, and choose a recovery or another qualified agent. ' +
        'Retry the failed worker only after identifying a recovery change that addresses the blocker. ' +
        'Ask the operator only for a specific action that requires their access or approval.'
      : `Perform your assigned step and include evidence. You may hand off to another qualified specialist through reply ${replyLimit - 2}. ` +
        'If blocked, describe the concrete cause, what you verified, and any useful partial work. ' +
        'Use a blocked TASK_OUTCOME for that step so the connector returns it to Manager to investigate. ' +
        'Leave routing and recovery to Manager. The penultimate reply must go to the coordinator.');
  }
  if (name === 'Manager') {
    return `This is reply ${event.number} of ${replyLimit}. Own the original request, set the acceptance checks, delegate only necessary work, and verify actual evidence before closing. ` +
      'Use HANDOFF: with one agent display name to delegate and get the result back. On the first reply, the optional final HANDOFF_ROUTE: JSON array ["Analyst","Coder","Reviewer","Coder"] authorizes that entire path when all three roles are needed. ' +
      'Give each recipient the goal, acceptance checks, and required evidence. A reviewer returns to the previous delegate. Use the last reply for the verified outcome or explain what blocks it.';
  }
  const index = event.number - start;
  const target = path[index] ?? manager;
  return `The Manager assigned this delegated step. Perform only the requested step and include the result, evidence, and any details the next agent needs. The connector will deliver your result to ${names[target]}. ` +
    'If blocked, report the concrete cause, verified evidence, and any useful partial work in the blocked TASK_OUTCOME form. ' +
    'The connector returns the blocker to Manager for investigation. Use no completed TASK_OUTCOME and leave routing to the connector.';
}

function delegatedBlockText(body, blocker) {
  return [
    body, '**Delegated work blocked**', `- **Blocker** ${blocker.blocker}`,
    `- **Who can help** ${blocker.unblocker}`, `- **Next step** ${blocker.action}`,
  ].filter(Boolean).join('\n\n');
}

export function managedReply(event, parts, peers, issues) {
  const { manager, path, start, dispatch } = event.managed;
  const participant = id => peers.find(peer => peer.id === id);
  const deliveryBlock = issues?.length && {
    status: 'blocked', blocker: issues[0], unblocker: 'The requester',
    action: 'Provide a smaller or readable file.',
  };
  if (event.managed.version === 2) {
    const coordinator = participant(manager);
    const blocker = deliveryBlock || parts.outcome?.status === 'blocked' && parts.outcome;
    const specialist = event.number < replyLimit - 1 && !blocker ? parts.target : null;
    const target = specialist ?? coordinator;
    if (target) {
      const body = blocker ? delegatedBlockText(parts.body, blocker) : parts.body;
      return { body, target, managed: { version: 2, manager, target: target.id, ...(dispatch && { dispatch }) } };
    }
    const outcome = blocker || parts.outcome || (event.number >= replyLimit - 1 && {
      status: 'blocked', blocker: 'The reply limit leaves no room for an additional delegation.',
      unblocker: 'The requester', action: 'Start a new assignment for the remaining work.',
    });
    return { body: parts.body, outcome };
  }
  if (participant(manager)) {
    const blocked = deliveryBlock || (parts.outcome?.status === 'blocked' && parts.outcome);
    const body = blocked ? delegatedBlockText(parts.body, blocked) : parts.body;
    return {
      body, target: participant(blocked ? manager : path[event.number - start] ?? manager),
      managed: { manager, path, start, dispatch, ...(blocked && { blocked: true }) },
    };
  }

  const number = event.number;
  const blocked = blocker => ({ body: parts.body, outcome: {
    status: 'blocked', blocker, unblocker: 'The requester',
    action: 'Mention Manager in this thread with the remaining work to start a new assignment.',
  } });
  if (deliveryBlock) return blocked(deliveryBlock.blocker);
  if (parts.handoff || parts.route !== undefined) {
    const target = peers.find(peer => peer.name === parts.handoff);
    const routeNames = parts.route === undefined ? [parts.handoff] : parts.route;
    const assigned = Array.isArray(routeNames) && routeNames.map(name => peers.find(peer => peer.name === name)?.id);
    const names = Object.fromEntries(peers.map(peer => [peer.id, peer.name]));
    if (!target || target.id !== assigned?.[0] || !validPath(assigned, number, manager, names)) {
      return blocked('The requested handoff could not fit the remaining turns or did not specify a valid route.');
    }
    return { body: parts.body, target, managed: { manager, start: number, path: assigned } };
  }
  if (!parts.outcome && number === replyLimit) return blocked(`The ${replyLimit}-reply limit was reached before the result was verified.`);
  return { body: parts.body, outcome: parts.outcome };
}
