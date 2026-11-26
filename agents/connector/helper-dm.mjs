import { createHash } from 'node:crypto';
import { writeFile, readFile, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import crypto from '@matrix-org/matrix-sdk-crypto-nodejs';
import { describeTurnFailure, runTurn } from './acp.mjs';
import { helperIdentity, helperQuestion, privateHelperRoom, supportEvidence } from './helper-support.mjs';
import { threadRelation } from './matrix-routing.mjs';
import { openRoomClient, readEncryptedRoomMessage, sendEncrypted } from './matrix-session.mjs';
import { Store } from './store.mjs';

const { MATRIX_URL: url, MATRIX_OWNER: owner, MATRIX_SERVER_NAME: server, AGENT_STATE: state } = process.env;
const userId = '@helper:' + server;
const roomId = (await readFile(join(state, 'room-id'), 'utf8')).trim();
const client = await openRoomClient({ url, state, roomId, userId, deviceId: 'MATRIXHELPERDM', name: 'Helper' });
const store = new Store(join(state, 'connector.sqlite3'));
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
  'or troubleshoot a service shown on the dashboard.';
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
  const deviceKey = digest(devices);
  const prior = JSON.parse(setting.get()?.value ?? '{}');
  const pinned = await client.getRoomStateEventContent(roomId, 'm.room.pinned_events', '').catch(error => {
    if (error.body?.errcode !== 'M_NOT_FOUND') throw error;
  });
  if (prior.key === deviceKey && pinned?.pinned?.includes(prior.event)) return;
  if (!await privateRoom()) return;
  const event = prior.key === deviceKey ? prior.event : await sendEncrypted(client, roomId, 'helper-welcome-' + deviceKey,
    { msgtype: 'm.text', body: welcome });
  if (!await privateRoom()) return;
  await client.doRequest('PUT', route + '/state/m.room.pinned_events', null, {
    pinned: [...new Set([...(pinned?.pinned ?? []).filter(id => id !== prior.event), event])],
  }, 15000);
  savePin.run(JSON.stringify({ key: deviceKey, event }));
}

let nextPin = 0;
while (true) {
  try {
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
          let reply = event.reply;
          if (reply === null) {
            try {
              reply = await runTurn({ workspace: '/workspace', event, store, identity: helperIdentity,
                guidance: await supportEvidence(event.body) });
            } catch (error) {
              reply = describeTurnFailure(error);
            }
            store.saveReply(event.id, reply);
          }
          if (await privateRoom()) {
            const sent = await sendEncrypted(client, roomId, 'helper-reply-' + digest(event.id),
              { msgtype: 'm.text', body: reply, 'm.relates_to': threadRelation(event) });
            store.sent(event.id, sent);
          }
        }
      }
    } else await rm('/tmp/matrix-agent-ready', { force: true });
  } catch (error) {
    console.warn('Helper will retry:', error.body?.errcode || error.code || error.name);
  }
  await delay(3000);
}
