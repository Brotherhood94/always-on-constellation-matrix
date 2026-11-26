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
const reconcile = () => reconcileTelegramRooms(client, reader, { space, domain, owner, guide });

const login = '2002';
const contact = '1001';
const agent = '@telegram-agent:telegram.test';

async function portal(peer, { metadata = {}, blocked = false } = {}) {
  const { room_id: room } = await post('createRoom', { ...options, initial_state: [{ type: 'm.room.encryption', state_key: '',
    content: { algorithm: 'm.megolm.v1.aes-sha2' } }] }, ownerToken);
  await bridge.query(`INSERT INTO portal (bridge_id,id,receiver,mxid,name,topic,avatar_id,avatar_hash,avatar_mxc,
    name_set,avatar_set,topic_set,in_space,room_type,metadata,user_blocked)
    VALUES ('',$1,$2,$3,'','','','','',false,false,false,false,'',$4,$5)`, [peer, login, room, metadata, blocked]);
  return room;
}

async function delivered(peer, id, sender, senderId, doublePuppet = false) {
  await bridge.query(`INSERT INTO message (bridge_id,id,part_id,mxid,room_id,room_receiver,sender_id,sender_mxid,
    timestamp,edit_count,double_puppeted,metadata) VALUES ('',$1,'',$2,$3,$4,$5,$6,0,0,$7,'{}')`,
  [id, `$fixture-${id}`, peer, login, senderId, sender, doublePuppet]);
}

const children = async () => (await client.getRoomState(space)).filter(event => event.type === 'm.space.child');
try {
  await bridge.query('INSERT INTO "user" (bridge_id,mxid) VALUES (\'\',$1) ON CONFLICT DO NOTHING', [owner]);
  await bridge.query('INSERT INTO user_login (bridge_id,user_mxid,id,remote_name,metadata) VALUES (\'\',$1,$2,\'\',\'{}\')', [owner, login]);
  await bridge.query(`INSERT INTO ghost (bridge_id,id,name,avatar_id,avatar_hash,avatar_mxc,name_set,avatar_set,
    contact_info_set,is_bot,identifiers,metadata)
    SELECT '',id,'','','','',false,false,false,false,'[]','{}' FROM unnest($1::text[]) AS id`, [[login, contact]]);

  const dm = await portal(`user:${contact}`);
  const group = await portal('chat:123');
  const supergroup = await portal('channel:456', { metadata: { is_supergroup: true } });
  await portal('channel:789', { metadata: { is_supergroup: true, is_forum_general: true } });
  await portal('channel:321');
  await portal('user:999', { blocked: true });
  await delivered(`user:${contact}`, 'inbound', `@telegram_${contact}:${domain}`, contact);
  await delivered(`user:${contact}`, 'relay', agent, login);
  await delivered(`user:${contact}`, 'double-puppet', owner, login, true);
  await reconcile();
  assert.deepEqual(await children(), []);

  await delivered(`user:${contact}`, 'owner-dm', owner, login);
  await delivered('chat:123', 'owner-group', owner, login);
  await delivered('channel:456', 'owner-supergroup', owner, login);
  await delivered('channel:789', 'owner-forum', owner, login);
  await delivered('channel:321', 'owner-broadcast', owner, login);
  await delivered('user:999', 'owner-blocked', owner, login);
  await assert.rejects(reader.query('SELECT * FROM message'), { code: '42501' });
  await reconcile();
  const baseline = await children();
  assert.deepEqual(baseline.map(event => event.state_key).sort(), [dm, group, supergroup].sort());
  assert.deepEqual(await client.getJoinedRooms(), [space]);
  await reconcile();
  assert.deepEqual((await children()).map(event => event.event_id).sort(), baseline.map(event => event.event_id).sort());

  const route = `/_matrix/client/v3/rooms/${encodeURIComponent(space)}/state/m.space.child/${encodeURIComponent(dm)}`;
  await client.doRequest('PUT', route, null, {});
  await reconcile();
  assert.deepEqual((await children()).find(event => event.state_key === dm).content, {});
  console.log('Confirmed operator sends linked the DM and groups once in Synapse. Incoming messages, agent sends and unsupported rooms stayed out.');
} finally {
  await reader.end();
  await bridge.end();
}
