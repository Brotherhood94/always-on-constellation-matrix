import { controlThread, matrixLink, operatorNotice } from './control.mjs';
import { telegramRestartHandoffKey } from '../../agents/connector/telegram-intent.mjs';
import { digest, pollEnd, pollStart } from './policy.mjs';

const windowMs = 15 * 60_000;
const reviewKey = 'org.matrixconstellation.telegram.admin_review';
const pollText = 'org.matrix.msc1767.text';
const eventId = /^\$[^\s\x00-\x1f]{1,254}$/u;
const actionName = {
  resync: 'Refresh Telegram group discovery and reconcile linked rooms.',
  disconnect: 'Disconnect the linked Telegram account. Linking again requires signing in on the VPS.',
  revoke: 'Revoke Telegram Agent access to the approved chat. Keep the operator’s room, history and Space link.',
};

export function createTelegramAdmin({ store, settings, matrix, guide, record, now = Date.now }) {
  const room = settings.controlRoom;
  store.db.exec(`CREATE TABLE IF NOT EXISTS telegram_admin_review (
    id TEXT PRIMARY KEY, thread TEXT NOT NULL, scope TEXT NOT NULL, requested INTEGER NOT NULL,
    proposal TEXT, poll TEXT UNIQUE, state TEXT NOT NULL DEFAULT 'pending', vote TEXT UNIQUE,
    result TEXT, notified INTEGER NOT NULL DEFAULT 0
  );`);
  store.run("UPDATE telegram_admin_review SET state='unknown', result=? WHERE state='applying'",
    'Telegram did not confirm the outcome before a restart. Check the current state before requesting another change.');
  const choiceId = (row, choice) => digest([row.id, row.scope, row.proposal, choice]);
  const send = async (id, stage, type, content) => {
    const ciphertext = await matrix.encryptEvent(room, type, content);
    if (!await matrix.verifyControl()) throw new Error('Control Room audience changed.');
    return matrix.put(room, 'telegram-admin-' + digest([id, stage]), ciphertext);
  };
  const pendingDelivery = binding => store.get(`SELECT 1 FROM draft
    WHERE state IN ('publishing','matrix_accepted','unknown') AND (? IS NULL OR binding=?) LIMIT 1`,
    binding ?? null, binding ?? null);

  async function request(task, scope) {
    if (!settings.controlAudience || !eventId.test(task.id) || !await matrix.verifyControl()) {
      return { message: 'Telegram administration requires the encrypted Control Room.' };
    }
    if (scope.action === 'restart') {
      const architect = settings.controlAudience.find(id => id === '@the-architect:' + settings.domain);
      if (!architect) return { message: 'Configure The Architect to review a bridge restart.' };
      const content = {
        ...operatorNotice(architect, 'Please review a restart of Telegram Bridge.'),
        [telegramRestartHandoffKey]: task.id,
        'm.relates_to': controlThread(task.thread, task.id),
      };
      const event = await send(task.id, 'architect', 'm.room.message', content);
      return { ok: true, message: 'The restart request was sent to The Architect. Review its approval poll in Control Room.\n' + matrixLink(room, event) };
    }
    const existing = store.get('SELECT 1 FROM telegram_admin_review WHERE id=?', task.id);
    if (existing) return { ok: true, message: 'This operation already has a Control Room review.' };
    const action = scope.action;
    if (!Object.hasOwn(actionName, action)) return { message: 'Unknown Telegram administration operation.' };
    const target = await guide({ id: task.id, action: action === 'revoke' ? 'admin-chat' : 'admin-account', target: scope.target });
    if (!target?.ok) return { message: target?.message ?? 'Telegram Bridge could not verify the selected target.' };
    let reviewed;
    if (action === 'revoke') {
      const binding = store.inRoom(target.room);
      if (binding && JSON.parse(binding.target).peer !== target.peer) return { message: 'The linked chat identity changed.' };
      reviewed = { action, room: target.room, peer: target.peer, name: target.name, binding: binding?.id ?? null,
        generation: binding?.generation ?? null, revision: binding?.revision ?? null };
    } else {
      reviewed = { action, login: target.login, revision: target.revision };
    }
    store.run('INSERT OR IGNORE INTO telegram_admin_review(id,thread,scope,requested) VALUES (?,?,?,?)',
      task.id, task.thread, JSON.stringify(reviewed), now());
    return { ok: true, message: 'Review the Telegram administration poll in Control Room.' };
  }

  function rememberPoll(event) {
    if (event.sender !== settings.agent || event.type !== pollStart || event.unsigned?.redacted_because) return;
    const request = event.content?.[reviewKey];
    if (typeof request !== 'string' || !eventId.test(request)) return;
    const row = store.get('SELECT * FROM telegram_admin_review WHERE id=?', request);
    if (!row?.proposal || row.poll || event.unsigned?.transaction_id !== 'telegram-admin-' + digest([request, 'poll'])) return;
    store.run('UPDATE telegram_admin_review SET poll=? WHERE id=? AND poll IS NULL', event.event_id, request);
  }

  async function accept(command, event) {
    if (command?.action !== 'review' || event.sender !== settings.owner) return false;
    const row = store.get('SELECT * FROM telegram_admin_review WHERE poll=?', command.poll);
    if (!row) return false;
    if (row.state !== 'pending' || now() >= row.requested + windowMs ||
        !Number.isSafeInteger(event.origin_server_ts) || event.origin_server_ts < row.requested ||
        event.origin_server_ts >= row.requested + windowMs || !await matrix.verifyControl()) return true;
    const decision = ['approve', 'decline'].find(choice => choiceId(row, choice) === command.choice);
    if (decision) store.run("UPDATE telegram_admin_review SET state=?, vote=? WHERE id=? AND state='pending'",
      decision === 'approve' ? 'approved' : 'declined', event.event_id, row.id);
    return true;
  }

  async function stillApproved(row) {
    const [source, proposal, poll, vote] = await Promise.all(
      [row.id, row.proposal, row.poll, row.vote].map(event => matrix.officeEvent(room, event)));
    const stamped = [source, proposal, poll, vote].map(event => event?.origin_server_ts);
    if (!source || ![settings.owner, '@dot:' + settings.domain].includes(source.sender) ||
        proposal?.sender !== settings.agent || poll?.sender !== settings.agent || vote?.sender !== settings.owner ||
        !stamped.every((stamp, index) => Number.isSafeInteger(stamp) && (!index || stamp >= stamped[index - 1]))) return false;
    const answers = poll.content?.[pollStart]?.answers?.map(answer => answer.id);
    const votes = vote.content?.[vote.type]?.answers ?? vote.content?.['m.selections'];
    return Array.isArray(answers) && answers.join() === ['approve', 'decline'].map(choice => choiceId(row, choice)).join() &&
      vote.content?.['m.relates_to']?.rel_type === 'm.reference' && vote.content['m.relates_to'].event_id === row.poll &&
      Array.isArray(votes) && votes.length === 1 && votes[0] === choiceId(row, 'approve');
  }

  async function apply(row, scope) {
    if (now() >= row.requested + windowMs) return { message: 'This approval expired before the operation could run.' };
    try {
      if (!await stillApproved(row) || !await matrix.verifyControl()) {
        return { message: 'The original encrypted approval could not be verified.' };
      }
    } catch {
      return { message: 'The original encrypted approval could not be read. Request a new review when Matrix is available.' };
    }
    if (scope.action !== 'resync' && pendingDelivery(scope.binding)) {
      return { message: 'Reconcile the in-flight Telegram delivery before changing access.' };
    }
    if (scope.action === 'revoke') {
      const binding = store.inRoom(scope.room);
      if ((binding?.id ?? null) !== scope.binding || (binding?.generation ?? null) !== scope.generation ||
          (binding?.revision ?? null) !== scope.revision) return { message: 'This chat’s binding changed after review. Request a new review.' };
      if (binding) store.pause(binding.id, 'operator_revoked');
    }
    store.run("UPDATE telegram_admin_review SET state='applying' WHERE id=?", row.id);
    const result = await guide({ id: row.id,
      action: scope.action === 'revoke' ? 'admin-revoke' : 'admin-' + scope.action,
      target: scope.room, peer: scope.peer, login: scope.login, revision: scope.revision }).catch(() => null);
    if (result?.ok && scope.action === 'disconnect') {
      for (const binding of store.bindings()) {
        if (JSON.parse(binding.target).login === scope.login) store.pause(binding.id, 'operator_disconnected');
      }
    }
    return result ?? { unknown: true, message: 'Telegram did not confirm the change. Check its status before retrying.' };
  }

  async function tick() {
    store.run("UPDATE telegram_admin_review SET state='expired' WHERE state='pending' AND requested<=?", now() - windowMs);
    const row = store.get('SELECT * FROM telegram_admin_review WHERE notified=0 AND (state<>\'pending\' OR poll IS NULL) ORDER BY requested LIMIT 1');
    if (!row || !await matrix.verifyControl()) return;
    const scope = JSON.parse(row.scope);
    const body = actionName[scope.action] + (scope.room ? '\nChat under review: ' + matrixLink(scope.room) : '');
    if (row.state === 'pending') {
      if (!row.proposal) {
        row.proposal = await send(row.id, 'proposal', 'm.room.message', {
          ...operatorNotice(settings.owner, body), 'm.relates_to': controlThread(row.thread, row.id),
        });
        store.run('UPDATE telegram_admin_review SET proposal=? WHERE id=?', row.proposal, row.id);
      }
      const question = actionName[scope.action] + (scope.name ? '\nChat: ' + scope.name : '') +
        '\nReview the exact proposal before voting.';
      const poll = await send(row.id, 'poll', pollStart, {
        'm.relates_to': controlThread(row.thread, row.proposal), 'm.mentions': { user_ids: [settings.owner] },
        [reviewKey]: row.id, [pollText]: question, [pollStart]: { kind: 'org.matrix.msc3381.poll.disclosed', max_selections: 1,
          question: { [pollText]: question }, answers: [
            { id: choiceId(row, 'approve'), [pollText]: '✅ Accept' },
            { id: choiceId(row, 'decline'), [pollText]: '❌ Decline' },
          ] },
      });
      store.run('UPDATE telegram_admin_review SET poll=? WHERE id=?', poll, row.id);
      return;
    }
    if (row.state === 'approved') {
      const result = await apply(row, scope);
      row.state = result.ok ? 'completed' : result.unknown ? 'unknown' : 'blocked';
      row.result = result.message;
      store.run('UPDATE telegram_admin_review SET state=?,result=? WHERE id=?', row.state, row.result, row.id);
    }
    if (row.poll) await send(row.id, 'close', pollEnd, {
      [pollText]: 'Telegram administration review closed.', [pollEnd]: {},
      'm.relates_to': { rel_type: 'm.reference', event_id: row.poll },
    });
    const result = row.result ?? (row.state === 'declined' ? 'Operation declined. No changes were applied.' : 'The review expired.');
    await send(row.id, 'result', 'm.room.message', { ...operatorNotice(settings.owner, result),
      'm.relates_to': controlThread(row.thread, row.poll ?? row.id) });
    record?.(row.id, { action: scope.action, status: row.state, poll: row.poll });
    store.run('UPDATE telegram_admin_review SET notified=1 WHERE id=?', row.id);
  }
  return { request, rememberPoll, accept, tick };
}
