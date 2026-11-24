import { catalogMembers, delegationGuidance, evaluateDelegation, gapKey } from './agent-catalog.mjs';
import { mentionMessage } from './matrix-format.mjs';

// Only trailing control lines are actionable. Other text is ordinary reply content.
export function controlsFromReply(reply) {
  const controls = {};
  let body = reply.trimEnd();
  let match;
  while ((match = body.match(/(?:^|\n)(HANDOFF|CAPABILITY_GAP|HANDOFF_ROUTE|TASK_OUTCOME|ATTACH_FILES):[ \t]+([^\r\n]*)$/u))) {
    body = body.slice(0, match.index).trimEnd();
    if (match[1] !== 'HANDOFF' && match[1] !== 'CAPABILITY_GAP') continue;
    const name = match[1] === 'HANDOFF' ? 'handoff' : 'gap';
    if (Object.hasOwn(controls, name)) continue;
    try {
      const parsed = JSON.parse(match[2]);
      controls[name] = parsed && typeof parsed === 'object' ? parsed : {};
    } catch {
      controls[name] = {};
    }
  }
  return controls;
}

export function dynamicTurn(catalog, event) {
  return !event.replyTo && (event.managed ? event.managed.version === 2 : catalog.current()?.dynamicHandoffs ?? catalog.configured);
}

export function guidanceFor(catalog, event, sender, managed) {
  return [managed, dynamicTurn(catalog, event) && delegationGuidance(catalog, sender)].filter(Boolean).join('\n\n');
}

export function routingFor(catalog, legacy, owner, planner) {
  const snapshot = catalog.current();
  const names = catalogMembers(snapshot, legacy);
  const coordinator = snapshot?.agents.find(agent => agent.role === 'coordinator')?.id;
  return { names, policy: { coordinator, dynamic: snapshot?.dynamicHandoffs ?? catalog.configured, activators: [owner, planner].filter(Boolean) } };
}

export async function decideDispatch(catalog, event, reply, actor, client, room) {
  if (!dynamicTurn(catalog, event)) return undefined;
  const controls = controlsFromReply(reply);
  if (!controls.handoff && !controls.gap) return {};
  await catalog.refresh(true);
  const joined = new Set(await client.getJoinedRoomMembers(room));
  return evaluateDelegation(catalog, actor, joined, controls);
}

export function gapNotice(event, decision, relation) {
  const gap = decision?.gap;
  if (!gap || !decision.observer) return undefined;
  const message = `Capability gap: ${gap.capability} on ${gap.resource}.\n${gap.reason}`;
  return { ...mentionMessage(message, decision.observer), [gapKey]: { request: event.request ?? event.id, ...gap },
    'm.relates_to': relation };
}

export function applyDispatch(parts, decision) {
  if (decision === undefined) return parts;
  return { ...parts, handoff: undefined, route: undefined, target: decision.target,
    outcome: decision.blocker ? { status: 'blocked', blocker: decision.blocker,
      unblocker: 'The requester', action: 'Review the requirement or choose how to proceed.' } : parts.outcome };
}
