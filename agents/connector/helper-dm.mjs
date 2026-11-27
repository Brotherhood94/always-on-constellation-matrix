import { createHash } from 'node:crypto';
import { writeFile, readFile, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import crypto from '@matrix-org/matrix-sdk-crypto-nodejs';
import { describeTurnFailure, runTurn } from './acp.mjs';
import { helperIdentity, helperQuestion, privateHelperRoom, supportEvidence } from './helper-support.mjs';
import { dailyHints, hintPreference } from './helper-hints.mjs';
import { mentionMessage } from './matrix-format.mjs';
import { threadRelation } from './matrix-routing.mjs';
import { acknowledgePrivateMessage, openRoomClient, readEncryptedRoomMessage, readOptional, sendEncrypted } from './matrix-session.mjs';
import { Store } from './store.mjs';

const { MATRIX_URL: url, MATRIX_OWNER: owner, MATRIX_SERVER_NAME: server, AGENT_STATE: state } = process.env;
const userId = '@helper:' + server;
const roomId = (await readFile(join(state, 'room-id'), 'utf8')).trim();
const client = await openRoomClient({ url, state, roomId, userId, deviceId: 'MATRIXHELPERDM', name: 'Helper' });
const store = new Store(join(state, 'connector.sqlite3'));
const hints = dailyHints(store.db, { timezone: process.env.HELPER_HINTS_TIMEZONE,
  features: (process.env.HELPER_HINTS_FEATURES ?? '').split(',') });
const activated = store.activatedAt();
const route = '/_matrix/client/v3/rooms/' + encodeURIComponent(roomId);
const privateRoom = async () => privateHelperRoom(await client.getRoomState(roomId), owner, userId);
const digest = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');

client.on('room.decrypted_event', (room, event) => {
  const question = helperQuestion({ ...event, room_id: room }, roomId, owner, activated);
  if (question) store.enqueue(question);
});
client.start().catch(error => {
  console.error('Helper Matrix sync stopped:', error.body?.errcode || error.code || error.name);
  process.exit(1);
});

const welcome = 'Ask me how to set up Element, connect Dot or Telegram, understand agent handoffs, ' +
  'or troubleshoot a service shown on the dashboard.\n\nTry asking “Come posso impostare Dot?”';
const setting = store.db.prepare("SELECT value FROM settings WHERE name='welcome'");
const savePin = store.db.prepare("INSERT OR REPLACE INTO settings VALUES ('welcome', ?)");

async function welcomePin() {
  const machine = client.crypto.engine.machine;
  const query = machine.queryKeysForUsers([new crypto.UserId(owner)]);
  const result = await client.doRequest('POST', '/_matrix/client/v3/keys/query', null, JSON.parse(query.body), 15000);
  await machine.markRequestAsSent(query.id, query.type, JSON.stringify(result));
  const devices = Object.entries(result.device_keys?.[owner] ?? {}).filter(([id, details]) =>
    details.user_id === owner && details.device_id === id && details.keys?.['curve25519:' + id] &&
    details.algorithms?.includes('m.megolm.v1.aes-sha2')).map(([id, details]) => details.keys['curve25519:' + id]).sort();
  if (!devices.length) return;
  const deliveryKey = digest({ devices, welcome });
  const prior = JSON.parse(setting.get()?.value ?? '{}');
  const pinned = await client.getRoomStateEventContent(roomId, 'm.room.pinned_events', '').catch(error => {
    if (error.body?.errcode !== 'M_NOT_FOUND') throw error;
  });
  if (prior.key === deliveryKey && pinned?.pinned?.includes(prior.event)) return;
  if (!await privateRoom()) return;
  const event = prior.key === deliveryKey ? prior.event : await sendEncrypted(client, roomId, 'helper-welcome-' + deliveryKey,
    { msgtype: 'm.text', body: welcome });
  if (!await privateRoom()) return;
  await client.doRequest('PUT', route + '/state/m.room.pinned_events', null, {
    pinned: [...new Set([...(pinned?.pinned ?? []).filter(id => id !== prior.event), event])],
  }, 15000);
  savePin.run(JSON.stringify({ key: deliveryKey, event }));
}

let nextPin = 0;
while (true) {
  try {
    const drain = await readOptional(join(state, 'relocation-drain'));
    if (drain) {
      await writeFile(join(state, 'relocation-drained'), drain, { mode: 0o600 });
      await delay(1000);
      continue;
    }
    if (await privateRoom()) {
      await writeFile('/tmp/matrix-agent-ready', 'helper', { mode: 0o644 });
      if (Date.now() >= nextPin) {
        nextPin = Date.now() + 30000;
        await welcomePin();
      }
      const event = store.next();
      if (event) {
        const original = await readEncryptedRoomMessage(client, roomId, event.id);
        if (original?.sender !== owner || original.content.msgtype !== 'm.text') {
          store.ignoreAssignment(event.id);
        } else {
          const clearActivity = await acknowledgePrivateMessage(client, roomId, event.id);
          let reply = event.reply;
          if (reply === null) {
            const preference = hintPreference(event.body);
            if (preference !== undefined) {
              hints.setEnabled(preference);
              reply = preference ? 'Daily hints are on.' : 'I’ll stop sending daily hints. Say “resume daily hints” to turn them back on.';
            } else try {
              reply = await runTurn({ workspace: '/workspace', event, store, identity: helperIdentity,
                guidance: await supportEvidence(event.body) });
            } catch (error) {
              reply = describeTurnFailure(error);
            }
            store.saveReply(event.id, reply);
          }
          if (await privateRoom()) {
            const sent = await sendEncrypted(client, roomId, 'helper-reply-' + digest(event.id),
              { ...mentionMessage(reply), 'm.relates_to': threadRelation(event) });
            await clearActivity();
            store.sent(event.id, sent);
          }
        }
      } else {
        await hints.tick(new Date(), (transaction, content) => sendEncrypted(client, roomId, transaction, content),
          () => Boolean(setting.get()) && privateRoom());
      }
    } else await rm('/tmp/matrix-agent-ready', { force: true });
  } catch (error) {
    console.warn('Helper will retry:', error.body?.errcode || error.code || error.name);
  }
  await delay(3000);
}
