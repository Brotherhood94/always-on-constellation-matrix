import crypto from '@matrix-org/matrix-sdk-crypto-nodejs';
import matrix from '@vector-im/matrix-bot-sdk';
import { mentionMessage } from './matrix-format.mjs';
import { sendEncrypted } from './matrix-session.mjs';

const input = [];
for await (const chunk of process.stdin) input.push(chunk);
const { token, owner, device, room, transaction, target, peer } = JSON.parse(Buffer.concat(input).toString('utf8'));
const client = new matrix.MatrixClient(
  'http://synapse:8008',
  token,
  new matrix.SimpleFsStorageProvider('/welcome/sync.json'),
  new matrix.RustSdkCryptoStorageProvider('/welcome/crypto', crypto.StoreType.Sqlite),
);
const identity = await client.getWhoAmI();
if (identity.user_id !== owner || identity.device_id !== device) {
  throw new Error('Unexpected Matrix welcome device');
}
const encryption = await client.getRoomStateEventContent(room, 'm.room.encryption', '');
if (encryption.algorithm !== 'm.megolm.v1.aes-sha2') {
  throw new Error('Hello Collaboration requires encrypted Office');
}
const instruction = peer
  ? `Hand off one contribution to ${peer.name} and keep the collaboration in this thread. ` +
    (target.role === 'coordinator' ? 'Review their contribution and close with a short welcome.' :
      'Ask them to post the finished welcome here after adding their contribution.')
  : 'Post a short welcome directly in this thread.';
const body = 'Hello Collaboration\n\nWelcome the new operator to Office. ' + instruction +
  ' Introduce the team members who participated and include one example of a first Office task. ' +
  'This welcome is a text-only exercise.';
let event;
try {
  await client.crypto.prepare();
  event = await sendEncrypted(client, room, transaction, mentionMessage(body, target));
} finally {
  // The one-shot client has no sync loop to own the lifetime of the native crypto store.
  client.crypto.engine?.machine.close();
}
console.log(JSON.stringify({ event }));
