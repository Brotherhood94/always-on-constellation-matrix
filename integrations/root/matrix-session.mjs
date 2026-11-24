import { open, readFile, rename } from 'node:fs/promises';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import crypto from '@matrix-org/matrix-sdk-crypto-nodejs';
import matrix from '@vector-im/matrix-bot-sdk';

const { MatrixClient, RustSdkCryptoStorageProvider, SimpleFsStorageProvider, LogService, LogLevel } = matrix;
LogService.setLevel(LogLevel.WARN);

class DurableMatrixClient extends MatrixClient {
  constructor(...args) {
    super(...args);
    this.persistTokenAfterSync = true;
  }
}

export async function readOptional(path) {
  try {
    return await readFile(path, 'utf8');
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
    return null;
  }
}

async function saveSecret(state, name, secret) {
  const path = join(state, name);
  const file = await open(path + '.tmp', 'w', 0o600);
  try {
    await file.writeFile(secret);
    await file.sync();
  } finally {
    await file.close();
  }
  await rename(path + '.tmp', path);
  const directory = await open(state, 'r');
  try {
    await directory.sync();
  } finally {
    await directory.close();
  }
}

async function accessToken({ url, state, userId, deviceId, name, token: settingsToken }) {
  if (settingsToken) return settingsToken;
  const stored = await readOptional(join(state, 'matrix-token'));
  if (stored) return stored.trim();
  const password = (await readFile(join(state, 'matrix-password'), 'utf8')).trim();
  const response = await fetch(url + '/_matrix/client/v3/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      type: 'm.login.password', identifier: { type: 'm.id.user', user: userId },
      password, device_id: deviceId, initial_device_display_name: `${name} Matrix Connector`,
    }),
    signal: AbortSignal.timeout(15000),
  });
  if (!response.ok) throw new Error('Matrix login HTTP ' + response.status);
  const login = await response.json();
  if (login.user_id !== userId || login.device_id !== deviceId || !login.access_token) {
    throw new Error('Unexpected Matrix login identity');
  }
  await saveSecret(state, 'matrix-token', login.access_token);
  return login.access_token;
}

export async function openRoomClient(settings) {
  const { url, state, roomId, userId, deviceId, name } = settings;
  const client = new DurableMatrixClient(
    url, await accessToken(settings), new SimpleFsStorageProvider(join(state, 'sync.json')),
    new RustSdkCryptoStorageProvider(join(state, 'crypto'), crypto.StoreType.Sqlite),
  );
  const identity = await client.getWhoAmI();
  if (identity.user_id !== userId || identity.device_id !== deviceId) throw new Error('Matrix device identity changed');
  if (!(await client.getJoinedRooms()).includes(roomId)) await client.joinRoom(roomId);
  const encryption = await client.getRoomStateEventContent(roomId, 'm.room.encryption', '');
  if (encryption.algorithm !== 'm.megolm.v1.aes-sha2') throw new Error('The Agents room must be encrypted');
  await client.crypto.prepare();
  await client.setDisplayName(name);
  if (!(await client.crypto.isRecoveryAvailable())) {
    await saveSecret(state, 'matrix-recovery-key', await client.crypto.createIdentity());
  }
  return client;
}

export async function sendEncrypted(client, roomId, transaction, content) {
  const encrypted = await client.crypto.encryptRoomEvent(roomId, 'm.room.message', content);
  const endpoint = `/_matrix/client/v3/rooms/${encodeURIComponent(roomId)}/send/m.room.encrypted/${transaction}`;
  const result = await client.doRequest('PUT', endpoint, null, encrypted, 15000);
  if (!result.event_id) throw new Error('Matrix did not confirm the message');
  return result.event_id;
}

export async function readEncryptedRoomMessage(client, roomId, eventId) {
  const raw = await client.getRawEvent(roomId, eventId).catch(error => {
    if (!['M_NOT_FOUND', 'M_FORBIDDEN'].includes(error.body?.errcode)) throw error;
  });
  if (raw?.type !== 'm.room.encrypted' || raw.unsigned?.redacted_because) return null;
  for (const wait of [0, 500, 1500]) {
    await delay(wait);
    try {
      const event = (await client.crypto.decryptRoomEvent(new matrix.EncryptedRoomEvent(raw), roomId)).raw;
      return event.type === 'm.room.message' ? { sender: raw.sender, content: event.content } : null;
    } catch (error) {
      if (error.code !== 'GenericFailure') throw error;
    }
  }
  return null;
}

export async function sendReaction(client, roomId, transaction, target, key) {
  const endpoint = `/_matrix/client/v3/rooms/${encodeURIComponent(roomId)}/send/m.reaction/${transaction}`;
  const result = await client.doRequest('PUT', endpoint, null, {
    'm.relates_to': { rel_type: 'm.annotation', event_id: target, key },
  }, 15000);
  if (!result.event_id) throw new Error('Matrix did not confirm the reaction');
  return result.event_id;
}

export async function redactReaction(client, roomId, transaction, reactionId) {
  const endpoint = `/_matrix/client/v3/rooms/${encodeURIComponent(roomId)}/redact/${encodeURIComponent(reactionId)}/${transaction}`;
  const result = await client.doRequest('PUT', endpoint, null, {}, 15000);
  if (!result.event_id) throw new Error('Matrix did not confirm the reaction removal');
}
