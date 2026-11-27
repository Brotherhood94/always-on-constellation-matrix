import { open, readFile, readdir, rename } from 'node:fs/promises';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import matrix from '@vector-im/matrix-bot-sdk';
import crypto from '@matrix-org/matrix-sdk-crypto-nodejs';
import { officeSetupNotice, reconcilePins, reconcileTelegramPin, shortcutRecipientDevices, shortcutRecipientKey } from './pins.mjs';
import { reconcileTelegramRooms } from './telegram-space.mjs';
import { listenTelegramOffice, privateTelegramControl } from './telegram-office.mjs';

const {
  LogService, LogLevel, MatrixClient, RustSdkCryptoStorageProvider,
  SimpleFsStorageProvider,
} = matrix;

const { MATRIX_URL, MATRIX_SERVER_NAME, MATRIX_OWNER, STATE_DIR, DATA_DIR, WORKSPACE_LINKS,
  TELEGRAM_GUIDE, GUIDE_COMMAND, GRAPH_PASSWORD_COMMAND, DOT_ENABLED, DOT_TUNNEL_SUPPORTED, SYSTEMD_ENABLED } = process.env;
const telegram = TELEGRAM_GUIDE === 'true';
const deviceId = telegram ? 'TELEGRAM_SETUP' : 'VPSMONITOR';
const userId = `@${telegram ? 'telegram-agent' : 'the-architect'}:${MATRIX_SERVER_NAME}`;
LogService.setLevel(LogLevel.WARN);

async function readOptional(path) {
  try { return await readFile(path, 'utf8'); }
  catch (error) {
    if (error.code === 'ENOENT') return null;
    throw error;
  }
}

async function writeAtomic(path, content) {
  const file = await open(path + '.tmp', 'w', 0o600);
  try {
    await file.writeFile(content);
    await file.sync();
  } finally {
    await file.close();
  }
  await rename(path + '.tmp', path);
  const folder = await open(path.substring(0, path.lastIndexOf('/')), 'r');
  try { await folder.sync(); }
  finally { await folder.close(); }
}

async function accessToken() {
  const path = join(STATE_DIR, 'token');
  const token = await readOptional(path);
  if (token) return token.trim();
  if (telegram) throw new Error('The Telegram setup device has not been provisioned');
  const password = (await readFile(join(STATE_DIR, 'password'), 'utf8')).trim();
  const response = await fetch(MATRIX_URL + '/_matrix/client/v3/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      type: 'm.login.password', identifier: { type: 'm.id.user', user: userId },
      password, device_id: deviceId, initial_device_display_name: 'Control Room',
    }),
    signal: AbortSignal.timeout(15000),
  });
  if (!response.ok) throw new Error('Matrix login HTTP ' + response.status);
  const login = await response.json();
  if (login.user_id !== userId || login.device_id !== deviceId || !login.access_token) {
    throw new Error('Unexpected Matrix login identity');
  }
  await writeAtomic(path, login.access_token);
  return login.access_token;
}

async function initialize() {
  const roomId = (await readFile(join(STATE_DIR, 'room-id'), 'utf8')).trim();
  const client = new MatrixClient(
    MATRIX_URL, await accessToken(),
    new SimpleFsStorageProvider(join(STATE_DIR, 'sync.json')),
    new RustSdkCryptoStorageProvider(join(STATE_DIR, 'crypto'), crypto.StoreType.Sqlite),
  );
  const identity = await client.getWhoAmI();
  if (identity.user_id !== userId || identity.device_id !== deviceId) {
    throw new Error('Matrix client identity changed');
  }
  if (!(await client.getJoinedRooms()).includes(roomId)) await client.joinRoom(roomId);
  const encryption = await client.getRoomStateEventContent(roomId, 'm.room.encryption', '');
  if (encryption.algorithm !== 'm.megolm.v1.aes-sha2') {
    throw new Error('The guide room requires Matrix encryption');
  }
  await client.crypto.prepare();
  if (!telegram) await client.setDisplayName('TheArchitect');
  if (!telegram && !(await client.crypto.isRecoveryAvailable())) {
    const key = await client.crypto.createIdentity();
    await writeAtomic(join(STATE_DIR, 'bot-recovery-key'), key);
  }
  client.start().catch(error => {
    console.error('Matrix sync stopped:', error.body?.errcode || error.code || error.name);
    process.exit(1);
  });
  return { client, roomId };
}

async function deliver({ client, roomId }) {
  const pending = join(DATA_DIR, 'outbox');
  const acknowledgements = join(DATA_DIR, 'acks');
  const queue = [];
  for (const name of await readdir(pending)) {
    if (!/^[a-f0-9]{32}\.json$/.test(name)) continue;
    const raw = await readOptional(join(pending, name));
    if (raw !== null) queue.push(JSON.parse(raw));
  }
  queue.sort((left, right) => left.time - right.time);
  for (const event of queue) {
    if (!/^[a-f0-9]{32}$/.test(event.id) || typeof event.body !== 'string') {
      throw new Error('Unexpected outbox event');
    }
    const ack = join(acknowledgements, event.id);
    if (await readOptional(ack)) continue;
    const payload = await client.crypto.encryptRoomEvent(roomId, 'm.room.message', {
      msgtype: 'm.text', body: event.body,
    });
    const endpoint = '/_matrix/client/v3/rooms/' + encodeURIComponent(roomId) +
      '/send/m.room.encrypted/' + event.id;
    const result = await client.doRequest('PUT', endpoint, null, payload, 15000);
    await writeAtomic(ack, result.event_id);
    console.log('Encrypted Matrix alert delivered:', event.id);
  }
}

async function shortcuts({ client, roomId }) {
  if (!WORKSPACE_LINKS && !telegram) return;
  const file = join(STATE_DIR, 'shortcut-pins.json');
  const state = JSON.parse(await readOptional(file) ?? '{"entries":{},"replaced":[]}');
  const route = '/_matrix/client/v3/rooms/' + encodeURIComponent(roomId);
  const pinRoom = {
    recipientKey: async () => {
      if (!(await client.getJoinedRoomMembers(roomId)).includes(MATRIX_OWNER)) return null;
      if (telegram && !(await privateTelegramControl(client, { control: roomId, domain: MATRIX_SERVER_NAME,
        owner: MATRIX_OWNER, guide: userId, bridgeControl: true }))) return null;
      return operatorDeviceKey(client);
    },
    getPinned: async () => {
      const current = await client.getRoomStateEventContent(roomId, 'm.room.pinned_events', '').catch(error => {
        if (error.body?.errcode !== 'M_NOT_FOUND') throw error;
      });
      return current?.pinned ?? [];
    },
    send: async (transaction, content) => {
      const encrypted = await client.crypto.encryptRoomEvent(roomId, 'm.room.message', content);
      const event = await client.doRequest('PUT', route + '/send/m.room.encrypted/' + transaction, null, encrypted, 15000);
      if (!event.event_id) throw new Error('Matrix did not confirm the shortcut');
      return event.event_id;
    },
    setPinned: pinned => client.doRequest('PUT', route + '/state/m.room.pinned_events', null, { pinned }, 15000),
    save: current => writeAtomic(file, JSON.stringify(current)),
  };
  if (telegram) {
    const command = (await readFile(join(STATE_DIR, 'command'), 'utf8')).trim();
    const legacy = (await readOptional(join(STATE_DIR, 'legacy-room-id')))?.trim();
    await reconcileTelegramPin(pinRoom, state, command, legacy);
  } else {
    const links = JSON.parse(await readFile(WORKSPACE_LINKS, 'utf8'));
    await reconcilePins(pinRoom, links, state, { command: GUIDE_COMMAND, graphPassword: GRAPH_PASSWORD_COMMAND,
      dot: DOT_ENABLED === 'true', dotTunnelSupported: (DOT_TUNNEL_SUPPORTED ?? SYSTEMD_ENABLED) === 'true' });
  }
}

async function operatorDeviceKey(client) {
  const machine = client.crypto.engine.machine;
  const query = machine.queryKeysForUsers([new crypto.UserId(MATRIX_OWNER)]);
  const result = await client.doRequest('POST', '/_matrix/client/v3/keys/query', null, JSON.parse(query.body), 15000);
  // The device may have uploaded its keys before the monitor's next sync.
  await machine.markRequestAsSent(query.id, query.type, JSON.stringify(result));
  const recipients = telegram ? shortcutRecipientDevices : shortcutRecipientKey;
  return recipients(MATRIX_OWNER, result.device_keys?.[MATRIX_OWNER]);
}

async function remindOffice({ client }) {
  const noticeFile = join(STATE_DIR, 'office-setup.json');
  const notice = JSON.parse(await readOptional(noticeFile) ?? 'null');
  if (!notice) return;
  const receipt = join(STATE_DIR, 'office-setup-receipt.json');
  const result = JSON.parse(await readOptional(receipt) ?? '{}');
  if (result.closed) return;
  const { room, pending, command } = notice;
  const route = '/_matrix/client/v3/rooms/' + encodeURIComponent(room);
  if (pending && !result.event) {
    if (!await operatorDeviceKey(client)) return;
    if (!(await client.getJoinedRooms()).includes(room)) await client.joinRoom(room);
    const encryption = await client.getRoomStateEventContent(room, 'm.room.encryption', '');
    if (encryption.algorithm !== 'm.megolm.v1.aes-sha2') throw new Error('Office setup requires an encrypted room');
    if (JSON.parse(await readOptional(noticeFile) ?? '{}').pending) {
      const encrypted = await client.crypto.encryptRoomEvent(room, 'm.room.message', officeSetupNotice(command));
      const { event_id } = await client.doRequest('PUT', route + '/send/m.room.encrypted/office-setup-v1', null, encrypted, 15000);
      if (!event_id) throw new Error('Matrix did not confirm the Office setup notice');
      result.event = event_id;
      await writeAtomic(receipt, JSON.stringify(result));
    }
  }
  await client.doRequest('POST', route + '/leave', null, {}, 15000);
  await writeAtomic(receipt, JSON.stringify({ ...result, closed: true }));
}

let spaceDatabase;
let spaceConnection;
let officeSocket;
let guideSocket;
async function telegramRooms({ client, roomId }) {
  const connection = (await readOptional(join(STATE_DIR, 'space-db')))?.trim();
  if (connection !== spaceConnection) {
    await spaceDatabase?.end();
    spaceDatabase = undefined;
    if (connection) {
      const { Pool } = await import('pg');
      spaceDatabase = new Pool({ connectionString: connection, max: 1,
        connectionTimeoutMillis: 5000, statement_timeout: 5000 });
      spaceDatabase.on('error', error => console.error('Telegram Space database connection:', error.code || error.name));
    }
    spaceConnection = connection;
  }
  const space = (await readFile(join(STATE_DIR, 'space-id'), 'utf8')).trim();
  const options = {
    space, domain: MATRIX_SERVER_NAME, owner: MATRIX_OWNER, guide: userId, control: roomId, bridgeControl: true,
  };
  const socketOptions = async () => ({ ...options, client, database: spaceDatabase,
    key: (await readOptional(join(STATE_DIR, 'office-bridge-key')))?.trim() });
  if (!officeSocket && process.env.TELEGRAM_OFFICE_SOCKET) {
    officeSocket = await listenTelegramOffice(process.env.TELEGRAM_OFFICE_SOCKET, socketOptions);
  }
  if (!guideSocket && process.env.TELEGRAM_GUIDE_SOCKET) {
    guideSocket = await listenTelegramOffice(process.env.TELEGRAM_GUIDE_SOCKET, socketOptions, { privateLookups: true });
  }
  if (spaceDatabase) await reconcileTelegramRooms(client, spaceDatabase, await socketOptions());
}

let sender;
let wait = 5000;
while (true) {
  try {
    sender ??= await initialize();
    if (!telegram) await deliver(sender);
    if (telegram) await telegramRooms(sender);
    await shortcuts(sender);
    if (!telegram) await remindOffice(sender);
    wait = 5000;
  } catch (error) {
    console.error('Matrix alert delivery retry:', error.body?.errcode || error.code || error.message);
    wait = Math.min(wait * 2, 60000);
  }
  await delay(wait);
}
