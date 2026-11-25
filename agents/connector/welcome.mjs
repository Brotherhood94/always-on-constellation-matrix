import crypto from '@matrix-org/matrix-sdk-crypto-nodejs';
import matrix from '@vector-im/matrix-bot-sdk';
import { mentionMessage } from './matrix-format.mjs';
import { sendEncrypted } from './matrix-session.mjs';

const input = [];
for await (const chunk of process.stdin) input.push(chunk);
const { token, sender, device, room, transaction, name, pins, example, dot } =
  JSON.parse(Buffer.concat(input).toString('utf8'));
const client = new matrix.MatrixClient(
  'http://synapse:8008',
  token,
  new matrix.SimpleFsStorageProvider('/welcome/sync.json'),
  new matrix.RustSdkCryptoStorageProvider('/welcome/crypto', crypto.StoreType.Sqlite),
);
const identity = await client.getWhoAmI();
if (identity.user_id !== sender || identity.device_id !== device) {
  throw new Error('Unexpected Matrix welcome device');
}
const encryption = await client.getRoomStateEventContent(room, 'm.room.encryption', '');
if (encryption.algorithm !== 'm.megolm.v1.aes-sha2') {
  throw new Error('The welcome requires an encrypted Matrix room');
}
const intro = 'Hi! I’m ' + name + '. Welcome to Constellation.';
const shortcuts = 'In Element, open Agents → VPS Alerts → Pinned messages for these shortcuts.';
const links = [['Open dashboard', pins.dashboard], ['Open Context Intelligence', pins.context]];
const next = [];
if (example) {
  const coordinated = example.role === 'coordinator';
  const task = (coordinated ? 'coordinate' : 'draft') + ' a three-step checklist for getting started in Office';
  if (coordinated) next.push(example.name + ' is the entry point for coordinated Office tasks.');
  next.push(
    'In Agents → Office, choose @' + example.name + ' from Element’s mention picker and send this example:\n' +
    '> @' + example.name + ', ' + task + ' and post the result in this thread.',
  );
  if (dot) {
    next.push(
      'In ChatGPT, open a conversation with the Constellation Office plugin and ask:\n' +
      '> Dot, send this task to Office starting with ' + example.name + ': ' + task +
      ' and post the result in the Office thread.',
    );
  }
} else {
  next.push('To submit a first task, add an Office agent from the VPS checkout with ' +
    'the agent add and agent configure commands. See docs/runbook.md, under Add a worker.');
}
const plain = links.map(([label, link]) => '- ' + label + ' — ' + link).join('\n');
const rich = links.map(([label, link]) => '- [' + label + '](' + link + ')').join('\n');
const content = mentionMessage([intro, shortcuts, plain, ...next].join('\n\n'), undefined,
  [intro, shortcuts, rich, ...next].join('\n\n'));
let event;
try {
  await client.crypto.prepare();
  event = await sendEncrypted(client, room, transaction, content);
} finally {
  // The one-shot client has no sync loop to own the lifetime of the native crypto store.
  client.crypto.engine?.machine.close();
}
console.log(JSON.stringify({ event }));
