import { createHash } from 'node:crypto';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { acceptedMessage, taskOutcomeKey, team, threadRelation, threadReply } from './matrix-routing.mjs';
import { createCatalog } from './agent-catalog.mjs';
import { decideDispatch, dynamicTurn, gapNotice, guidanceFor, routingFor } from './capability-dispatch.mjs';
import { managedContext, managedGuidance } from './managed-task.mjs';
import { openRoomClient, readEncryptedRoomMessage, redactReaction, sendEncrypted, sendReaction } from './matrix-session.mjs';
import { Store } from './store.mjs';
import { runTurn } from './acp.mjs';

const root = process.env.EPYC_AGENT_HOME;
const workspace = process.env.EPYC_AGENT_WORKSPACE;
const host = process.env.EPYC_MATRIX_SERVER_NAME;
const url = `https://${host}`;
const state = join(root, 'state');
const userId = `@epyc100:${host}`;
const owner = process.env.EPYC_MATRIX_OWNER;
const planner = `@dot:${host}`;
const deviceId = 'EPYC100AMPLIFIER';
const catalog = createCatalog(host, `${url}:8443/agents.json`);
const legacy = { ...team(host), [userId]: 'Epyc100' };
const routing = () => routingFor(catalog, legacy, owner, planner);
await catalog.refresh();
const outcomeRecipients = [{ id: planner, name: 'Dot' }, { id: owner, name: 'Operator' }];

const roomAlias = encodeURIComponent(`#agents:${host}`);
const lookup = await fetch(`${url}/_matrix/client/v3/directory/room/${roomAlias}`, {
  signal: AbortSignal.timeout(15000),
});
if (!lookup.ok) throw new Error(`Matrix room lookup HTTP ${lookup.status}`);
const { room_id: roomId } = await lookup.json();
if (typeof roomId !== 'string' || !roomId.startsWith('!')) throw new Error('Invalid Matrix room ID');

const store = new Store(join(state, 'epyc100.sqlite3'));
store.db.exec('CREATE TABLE IF NOT EXISTS attempted_turns (event_id TEXT PRIMARY KEY)');
const claim = store.db.prepare('INSERT OR IGNORE INTO attempted_turns VALUES (?)');
const activatedAt = store.activatedAt();
const client = await openRoomClient({ url, state, roomId, userId, deviceId, name: 'Epyc100' });
client.on('room.message', (incomingRoom, event) => {
  const { names, policy } = routing();
  const task = acceptedMessage({ ...event, room_id: incomingRoom }, {
    roomId, owner, planner, userId, agents: Object.keys(names), activatedAt, displayNames: names,
    routingPolicy: policy, acceptAttachments: false,
  });
  if (task) store.enqueue(task);
});
client.start().catch(error => {
  console.error('Matrix sync stopped:', error.body?.errcode || error.code || error.name);
  process.exit(1);
});
console.log('Epyc100 is listening for explicit Matrix mentions using the shared Agents policies.');

function reactionTransaction(operation, target, key) {
  return createHash('sha256').update(JSON.stringify([operation, target, key])).digest('hex');
}

function rateLimitDelay(error) {
  const retryAfter = error.body?.retry_after_ms;
  if (error.body?.errcode === 'M_LIMIT_EXCEEDED' && Number.isSafeInteger(retryAfter)) {
    return Math.max(retryAfter + 100, 1000);
  }
}

async function publishActivity(id) {
  if (!store.activityPending(id)) return;
  const key = '👀';
  const transaction = reactionTransaction('activity', id, key);
  store.activitySent(id, await sendReaction(client, roomId, transaction, id, key));
}

async function trackActivity(id, signal) {
  store.startActivity(id);
  let retry = 1000;
  while (!signal.aborted) {
    try {
      await publishActivity(id);
      return;
    } catch (error) {
      console.error('Matrix activity reaction retry:', error.body?.errcode || error.code || error.name);
      await delay(rateLimitDelay(error) ?? retry, undefined, { signal }).catch(interruption => {
        if (interruption.name !== 'AbortError') throw interruption;
      });
      retry = Math.min(retry * 2, 10000);
    }
  }
}

async function settleReactions() {
  const pending = store.nextReactionCleanup();
  if (!pending) return;
  if (pending.activityId && !pending.activityRemoved) {
    const transaction = reactionTransaction('remove', pending.activityId);
    await redactReaction(client, roomId, transaction, pending.activityId);
    store.activityRemoved(pending.id);
  }
  if (pending.outcomeKey && !pending.outcomeId) {
    const transaction = reactionTransaction('outcome', pending.request, pending.outcomeKey);
    store.outcomeSent(pending.id, await sendReaction(client, roomId, transaction, pending.request, pending.outcomeKey));
  }
}

function interruptedReply(task, blocker) {
  const action = 'Controlla lo stato dell’operazione su epyc100 prima di chiedermi di ripeterla.';
  const note = task.replyTo ? '' : '\nTASK_OUTCOME: ' + JSON.stringify({
    status: 'blocked', blocker, unblocker: 'The operator', action,
  });
  return `${blocker} ${action}${note}`;
}

async function deliver(task) {
  const { names: displayNames } = routing();
  const peers = Object.entries(displayNames).filter(([id]) => id !== userId).map(([id, name]) => ({ id, name }));
  let reply = task.reply;
  let activity;
  const timer = new AbortController();
  if (reply === null) {
    const context = await managedContext(task, displayNames, id => readEncryptedRoomMessage(client, roomId, id));
    if (!context) {
      store.ignoreAssignment(task.id);
      console.warn('Ignored a Matrix handoff with an unverifiable Manager route.');
      return;
    }
    if (!claim.run(task.id).changes) {
      reply = interruptedReply(task, 'Il servizio si è interrotto prima di confermare il risultato.');
    } else {
      const onStarted = task.replyTo ? undefined : () => {
        activity = trackActivity(task.id, timer.signal).catch(error => {
          console.error('Matrix activity tracking failed:', error.body?.errcode || error.code || error.name);
        });
      };
      try {
        reply = await runTurn({
          instanceCommand: join(root, 'bin', 'agent-instance'),
          runtimeHome: join(root, 'instance'),
          workspace, event: context, store, peers: dynamicTurn(catalog, task) ? [] : peers, onStarted,
          guidance: guidanceFor(catalog, task, userId, managedGuidance(task, 'Epyc100', displayNames, userId)),
        });
      } catch (error) {
        console.error('Constellation turn failed:', error.code || error.name);
        reply = interruptedReply(task, 'Amplifier si è interrotto prima di confermare il risultato.');
      } finally {
        timer.abort();
      }
    }
    store.saveReply(task.id, reply);
  }
  await activity;
  await publishActivity(task.id);
  const requesterName = outcomeRecipients.find(recipient => recipient.id === task.replyTo)?.name;
  let outgoing = task.outgoing;
  if (!outgoing) {
    const decision = await decideDispatch(catalog, task, reply, userId, client, roomId);
    const content = threadReply(task, reply, peers, requesterName, outcomeRecipients, {}, decision);
    outgoing = { content, notice: gapNotice(task, decision, threadRelation(task)) };
    store.saveOutgoing(task.id, outgoing);
  }
  const { content, notice } = outgoing;
  const outcomeKey = { completed: '✅', blocked: '❌' }[content[taskOutcomeKey]?.status];
  const transaction = createHash('sha256').update(task.id).digest('hex');
  const sent = await sendEncrypted(client, roomId, transaction, content);
  if (notice) await sendEncrypted(client, roomId, reactionTransaction('capability-gap', task.id), notice);
  store.sent(task.id, sent, outcomeKey);
}

while (true) {
  try {
    await catalog.refresh();
    const task = store.next();
    if (task) await deliver(task);
    await settleReactions();
    await delay(750);
  } catch (error) {
    console.error('Matrix connector retry:', error.body?.errcode || error.code || error.name);
    await delay(rateLimitDelay(error) ?? 5000);
  }
}
