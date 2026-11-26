import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import matrix from '@vector-im/matrix-bot-sdk';
import pg from 'pg';
import { reconcileTelegramRooms } from './telegram-space.mjs';

const domain = 'telegram.test';
const matrixUrl = 'http://synapse:8008';
const accounts = JSON.parse(await readFile('/fixture/database.json', 'utf8'));
const database = role => new pg.Pool({ host: 'postgres', database: 'matrix_telegram', user: role, password: accounts[role] });
const bridge = database('matrix_telegram_bridge');
const reader = database('matrix_telegram_space');

async function post(path, body, token) {
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(`${matrixUrl}/_matrix/client/v3/${path}`, { method: 'POST', headers, body: JSON.stringify(body) });
  assert.ok(response.ok, `${path} returned ${response.status}`);
  return response.json();
}

const options = { preset: 'private_chat', creation_content: { 'm.federate': false } };
const ownerSession = await post('login', { type: 'm.login.password', identifier: { type: 'm.id.user', user: 'operator' },
  password: 'fixture-long-password' });
const guideSession = await post('register', { username: 'space-guide', password: 'fixture-long-password', auth: { type: 'm.login.dummy' } });
const { user_id: owner, access_token: ownerToken } = ownerSession;
const { user_id: guide, access_token: guideToken } = guideSession;
const { room_id: space } = await post('createRoom', { ...options, creation_content: { ...options.creation_content, type: 'm.space' },
  invite: [owner] }, guideToken);
await post(`join/${encodeURIComponent(space)}`, {}, ownerToken);
const client = new matrix.MatrixClient(matrixUrl, guideToken);
const ownerClient = new matrix.MatrixClient(matrixUrl, ownerToken);
const reconcile = () => reconcileTelegramRooms(client, reader, { space, domain, owner, guide });

const login = '2002';
const otherLogin = '2003';

async function portal(peer, { metadata = {}, blocked = false, receiver = login, opened = true } = {}) {
  const { room_id: room = null } = opened ? await post('createRoom', { ...options,
    initial_state: [{ type: 'm.room.encryption', state_key: '', content: { algorithm: 'm.megolm.v1.aes-sha2' } }],
  }, ownerToken) : {};
  await bridge.query(`INSERT INTO portal (bridge_id,id,receiver,mxid,name,topic,avatar_id,avatar_hash,avatar_mxc,
    name_set,avatar_set,topic_set,in_space,room_type,metadata,user_blocked)
    VALUES ('',$1,$2,$3,'','','','','',false,false,false,false,'',$4,$5)`, [peer, receiver, room, metadata, blocked]);
  return room;
}

const children = async () => (await ownerClient.getRoomState(space)).filter(event => event.type === 'm.space.child');
try {
  for (const [user, account] of [[owner, login], [`@another-operator:${domain}`, otherLogin]]) {
    await bridge.query(`INSERT INTO "user" (bridge_id,mxid) VALUES ('',$1) ON CONFLICT DO NOTHING`, [user]);
    await bridge.query(`INSERT INTO user_login (bridge_id,user_mxid,id,remote_name,metadata) VALUES ('',$1,$2,'','{}')`, [user, account]);
  }

  const dm = await portal('user:1001');
  const group = await portal('chat:123');
  const supergroup = await portal('channel:456', { metadata: { is_supergroup: true } });
  await portal('channel:789', { metadata: { is_supergroup: true, is_forum_general: true } });
  await portal('channel:321');
  await portal('user:999', { blocked: true });
  await portal('user:1001', { receiver: otherLogin });
  await portal('user:1002', { opened: false });
  const { room_id: oldDirect } = await post('createRoom', options, ownerToken);
  const direct = { [`@telegram_1001:${domain}`]: [oldDirect] };
  const accountData = `/_matrix/client/v3/user/${encodeURIComponent(owner)}/account_data/m.direct`;
  await ownerClient.doRequest('PUT', accountData, null, direct);
  await assert.rejects(reader.query('SELECT * FROM message'), { code: '42501' });
  assert.deepEqual(await children(), []);
  await reconcile();
  const baseline = await children();
  assert.deepEqual(baseline.map(event => event.state_key).sort(), [dm, group, supergroup].sort(),
    'Opening a bridged room should link it to Telegram before any message is sent');
  assert.ok(baseline.every(event => event.content.via.includes(domain)));
  assert.deepEqual(await ownerClient.doRequest('GET', accountData), direct);
  assert.equal((await ownerClient.getRoomStateEventContent(dm, 'm.room.encryption', '')).algorithm, 'm.megolm.v1.aes-sha2');
  assert.deepEqual(await client.getJoinedRooms(), [space]);
  await reconcile();
  assert.deepEqual((await children()).map(event => event.event_id).sort(), baseline.map(event => event.event_id).sort());

  const route = `/_matrix/client/v3/rooms/${encodeURIComponent(space)}/state/m.space.child/${encodeURIComponent(dm)}`;
  await client.doRequest('PUT', route, null, {});
  await reconcile();
  assert.deepEqual((await children()).find(event => event.state_key === dm).content, {});
  console.log('Opening the contact and groups linked them once in Synapse before any sends. Other logins, cached peers and unsupported rooms stayed out.');
} finally {
  await reader.end();
  await bridge.end();
}
