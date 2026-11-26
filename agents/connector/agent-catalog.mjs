import { readFile } from 'node:fs/promises';

export const gapKey = 'net.matrix_agents.capability_gap';
export const agentKeyPattern = /^[a-z0-9][a-z0-9_-]{0,63}$/u;
const resourcePattern = /^[a-z][a-z0-9-]{0,30}:[a-zA-Z0-9][a-zA-Z0-9._/-]{0,119}$/u;
const scopePattern = /^[a-z][a-z0-9-]{0,30}:\*$/u;
const maxAge = 180000;
const dispatchable = new Set(['Available', 'Running', 'Registered']);

export function requirement(value) {
  if (typeof value?.capability !== 'string' || !agentKeyPattern.test(value.capability) ||
      typeof value.resource !== 'string' || !resourcePattern.test(value.resource)) return null;
  return { capability: value.capability, resource: value.resource };
}

export function matchingAgents(snapshot, wanted, sender) {
  if (!wanted) return [];
  return snapshot.agents.filter(agent => agent.id !== sender && agent.role !== 'observer' && agent.capabilities.some(skill =>
    skill.code === wanted.capability && skill.resources.some(scope => scope === wanted.resource ||
      (scopePattern.test(scope) && wanted.resource.startsWith(scope.slice(0, -1))))));
}

export function canContact(snapshot, sender, target) {
  const source = snapshot.agents.find(agent => agent.id === sender);
  if (source?.key === 'helper') return false;
  if (target.key === 'helper') return source?.role === 'coordinator' && ['task-manager', 'manager'].includes(source.key);
  return !snapshot.contactPolicy || Boolean(snapshot.agents.find(agent => agent.id === sender)?.contacts?.includes(target.key));
}

export function acceptsContact(policy, event, recipient) {
  const snapshot = policy?.snapshot;
  const target = snapshot?.agents.find(agent => agent.id === recipient);
  const turn = event.content?.['net.matrix_agents.turn'];
  if (recipient.startsWith('@helper:')) {
    return Boolean(target?.active && policy.fresh && dispatchable.has(target.status) &&
      !turn?.returning && canContact(snapshot, event.sender, target) &&
      turn?.handoff?.capability === 'constellation-support' && turn.handoff.resource === 'workspace:constellation-matrix' &&
      matchingAgents(snapshot, requirement(turn.handoff), event.sender).some(agent => agent.id === recipient));
  }
  if (!snapshot?.contactPolicy) return true;
  // The connector verifies the original assignment chain before accepting a return.
  if (turn?.returning === true) return !turn.handoff && (!turn.managed || (turn.managed.version === 2 && turn.managed.manager === recipient));
  const wanted = requirement(turn?.handoff);
  return policy.fresh && target?.active !== false && dispatchable.has(target?.status) && canContact(snapshot, event.sender, target) &&
    matchingAgents(snapshot, wanted, event.sender).some(agent => agent.id === recipient);
}

export function catalogMembers(snapshot, legacy) {
  if (!snapshot) return legacy;
  const members = Object.fromEntries(snapshot.agents.filter(agent => agent.role !== 'observer' && agent.active !== false).map(agent => [agent.id, agent.name]));
  return snapshot.dynamicHandoffs ? members : { ...legacy, ...members };
}

export function parseCatalog(text, server) {
  if (text.length > 2_000_000) throw new Error('Agent catalog exceeds its size limit');
  const value = JSON.parse(text);
  const roles = new Set(['worker', 'coordinator', 'observer']);
  if (value?.version !== 1 || !Number.isSafeInteger(value.time) || typeof value.dynamicHandoffs !== 'boolean' ||
      ('contactPolicy' in value && typeof value.contactPolicy !== 'boolean') ||
      ('policyRevision' in value && (typeof value.policyRevision !== 'string' || !/^[a-f0-9]{32}$/u.test(value.policyRevision))) ||
      !Array.isArray(value.agents) || value.agents.length > 128 ||
      !value.agents.every(agent => typeof agent.key === 'string' && agentKeyPattern.test(agent.key) &&
        (!value.contactPolicy || Array.isArray(agent.contacts) && agent.contacts.every(key => typeof key === 'string' && agentKeyPattern.test(key))) &&
        agent.id === `@${agent.key}:${server}` && typeof agent.name === 'string' && roles.has(agent.role) &&
        typeof agent.limits === 'string' && typeof agent.status === 'string' && Array.isArray(agent.capabilities) &&
        agent.capabilities.every(skill => typeof skill.code === 'string' && agentKeyPattern.test(skill.code) &&
          typeof skill.description === 'string' && Array.isArray(skill.resources) &&
          skill.resources.every(scope => typeof scope === 'string' && (resourcePattern.test(scope) || scopePattern.test(scope))))) ||
      new Set(value.agents.map(agent => agent.key)).size !== value.agents.length ||
      ['coordinator', 'observer'].some(role => value.agents.filter(agent => agent.role === role).length > 1)) {
    throw new Error('Invalid agent catalog');
  }
  return value;
}

export function createCatalog(server, source) {
  let snapshot;
  let lastAttempt = -Infinity;
  let pending;
  return {
    configured: Boolean(source),
    current: () => snapshot,
    fresh: () => Boolean(snapshot && Math.abs(Date.now() - snapshot.time * 1000) <= maxAge),
    async refresh(force = false) {
      if (!source || (!force && Date.now() - lastAttempt < 10000)) return pending;
      if (pending) return pending;
      lastAttempt = Date.now();
      pending = (async () => {
        try {
          let text;
          if (source.startsWith('/')) text = await readFile(source, 'utf8');
          else {
            const endpoint = new URL(source);
            if (endpoint.protocol !== 'https:' || endpoint.hostname !== server || endpoint.pathname !== '/agents.json' ||
                endpoint.username || endpoint.password || endpoint.search || endpoint.hash) {
              throw new Error('Unexpected agent catalog endpoint');
            }
            const response = await fetch(source, { redirect: 'error', signal: AbortSignal.timeout(5000), cache: 'no-store' });
            if (!response.ok) throw new Error(`Agent catalog HTTP ${response.status}`);
            text = await response.text();
          }
          snapshot = parseCatalog(text, server);
        } catch (error) {
          console.warn(`Could not refresh the agent catalog: ${error.code || error.name}`);
        } finally {
          pending = undefined;
        }
      })();
      return pending;
    },
  };
}

export function canDispatch(agent, joined) {
  return agent?.role !== 'observer' && Boolean(agent && dispatchable.has(agent.status) && joined.has(agent.id));
}

export function delegationGuidance(catalog, sender) {
  if (!catalog.fresh()) return 'The agent catalog is unavailable. Do not guess a recipient or claim a capability is missing.';
  const profiles = catalog.current().agents.filter(agent => agent.id !== sender && agent.role !== 'observer' && canContact(catalog.current(), sender, agent));
  return 'Choose a colleague by the capability and resource scope below. Include the requested work, reason for the choice, and evidence needed. ' +
    'Use one final control line: HANDOFF: {"agent":"catalog-key","capability":"capability-code","resource":"type:resource"}. ' +
    'If no registered capability covers an essential next step, use CAPABILITY_GAP: {"capability":"missing-code","resource":"type:resource","reason":"what work this prevents"}. ' +
    'If your own registered duty is blocked by missing tooling, expired credentials or a temporary outage, add OFFICE_NEED: {"kind":"tool|credentials|outage","capability":"your-code","topic":"public tool keywords"} before TASK_OUTCOME. ' +
    'Do not use CAPABILITY_GAP for a temporary outage, a request for permissions, or a missing prerequisite. Use no control line if you can answer or complete the task yourself.\n' +
    JSON.stringify(profiles.map(({ key, role, status, limits, capabilities }) => ({ key, role, status, limits, capabilities })));
}

export function evaluateDelegation(catalog, sender, joined, controls) {
  if (!controls) return {};
  const { handoff, gap } = controls;
  if (!handoff && !gap) return {};
  if (!catalog.fresh()) return { blocker: 'The agent catalog is unavailable or stale.' };
  if (handoff && gap) return { blocker: 'Choose either a handoff or a capability gap.' };
  const wanted = requirement(handoff ?? gap);
  if (!wanted) return { blocker: 'The handoff did not specify a valid capability and resource.' };
  const snapshot = catalog.current();
  const matches = matchingAgents(snapshot, wanted, gap ? undefined : sender);
  if (gap) {
    if (matches.some(agent => canDispatch(agent, joined))) return { blocker: 'A registered agent covers this capability.' };
    if (matches.length) return { blocker: 'Agents with the requested capability are temporarily unavailable for dispatch.' };
    if (typeof gap.reason !== 'string' || !gap.reason.trim() || gap.reason.length > 400) {
      return { blocker: 'A capability gap needs a concise explanation.' };
    }
    const observer = snapshot.agents.find(agent => agent.roleId === 'hiring-manager' && agent.active !== false) ?? snapshot.agents.find(agent => agent.role === 'observer');
    return {
      blocker: `No registered agent covers ${wanted.capability} on ${wanted.resource}.`,
      ...((observer || snapshot.contactPolicy) && { gap: { ...wanted, reason: gap.reason.trim() }, observer }),
    };
  }
  const target = matches.find(agent => agent.key === handoff.agent);
  if (!target) return { blocker: 'The selected agent does not cover the required capability and resource.' };
  if (!canContact(snapshot, sender, target)) return { blocker: 'The selected agent is outside your permitted contacts.' };
  if (!canDispatch(target, joined)) return { blocker: 'The selected agent is unavailable or outside the Agents room.' };
  return snapshot.contactPolicy || target.key === 'helper' ? { target, requirement: wanted,
    ...(snapshot.policyRevision && { policyRevision: snapshot.policyRevision }) } : { target };
}
