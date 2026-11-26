import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { access, mkdir, readFile, unlink, writeFile } from 'node:fs/promises';
import { createServer } from 'node:https';
import { setTimeout as delay } from 'node:timers/promises';
import { WebSocketServer } from 'ws';
import { openRoomClient, sendEncrypted } from './matrix-session.mjs';

const fixture = JSON.parse(await readFile('/fixture/helper-smoke.json', 'utf8'));
const { owner, privateRoom, domain, operator, helperToken } = fixture;
const url = 'http://synapse:8008';
const clientSecret = 'synthetic-helper-acp';
const sockets = new Set();
const turns = [];
const environment = { ...process.env, MATRIX_URL: url, MATRIX_SERVER_NAME: domain, MATRIX_OWNER: owner,
  INSTANCE_ACP_URL: 'wss://localhost:19443/api/v1/acp', AGENT_STATE: '/state' };
for (const path of ['/state', '/operator', '/workspace', '/catalog', '/support', '/acp']) await mkdir(path, { recursive: true });
await writeFile('/state/matrix-token', helperToken);
await writeFile('/state/room-id', privateRoom);
await writeFile('/operator/matrix-token', operator.access_token);
await writeFile('/acp/credential-token', clientSecret);
await writeFile('/support/guides.json', JSON.stringify({ revision: 'isolated-revision', documents: [{
  path: 'docs/setup-guide.md', link: 'https://example.test/docs/setup-guide.md',
  body: '# Set up Element\nClick Edit to enter the workspace homeserver.',
}] }));
await writeFile('/catalog/integrations.json', JSON.stringify({
  time: Math.floor(Date.now() / 1000) - 250, services: { 'Context Intelligence': 'healthy' },
}));
const ca = await readFile('/helper-tls/cert.pem');
await writeFile('/acp/ca.pem', ca);
const https = createServer({ key: await readFile('/helper-tls/key.pem'), cert: ca });
const acp = new WebSocketServer({ server: https, path: '/api/v1/acp' });
acp.on('connection', (socket, request) => {
  assert.equal(request.headers.authorization, 'Bearer ' + clientSecret);
  sockets.add(socket);
  socket.once('close', () => sockets.delete(socket));
  socket.on('message', raw => {
    const { id, method, params } = JSON.parse(raw);
    const send = message => socket.send(JSON.stringify({ jsonrpc: '2.0', ...message }));
    if (method === 'initialize') send({ id, result: { protocolVersion: params.protocolVersion, agentCapabilities: { loadSession: true } } });
    if (method === 'session/new') send({ id, result: { sessionId: 'helper-fixture-' + turns.length } });
    if (method === 'session/prompt') {
      const prompt = params.prompt[0].text;
      turns.push(prompt);
      const text = prompt.includes('Element Desktop?')
        ? 'Click Edit and enter the workspace homeserver. See https://example.test/docs/setup-guide.md'
        : 'Context Intelligence was healthy in the stale snapshot.';
      send({ method: 'session/update', params: { sessionId: params.sessionId, update: {
        sessionUpdate: 'agent_message_chunk', content: { type: 'text', text },
      } } });
      send({ id, result: { stopReason: 'end_turn' } });
    }
  });
});
await new Promise(resolve => https.listen(19443, resolve));
const client = await openRoomClient({ url, state: '/operator', roomId: privateRoom,
  userId: owner, deviceId: operator.device_id, name: 'Fixture Operator' });
const observed = [];
client.on('room.decrypted_event', (room, event) => { if (room === privateRoom) observed.push(event); });
client.start().catch(error => { console.error(error.name); process.exit(1); });

async function until(check, label) {
  for (let attempt = 0; attempt < 420; attempt++) {
    const result = await check();
    if (result) return result;
    await delay(100);
  }
  throw new Error('Timed out waiting for ' + label);
}
const byBody = body => observed.find(event => event.content?.body?.includes(body));
const helper = spawn(process.execPath, ['/app/helper-dm.mjs'], { env: environment, stdio: 'inherit' });
try {
  await until(() => access('/tmp/matrix-agent-ready').then(() => true, () => false), 'Helper readiness');
  const welcome = await until(() => byBody('Ask me how to set up Element'), 'the decrypted welcome');
  assert.deepEqual((await client.getRoomStateEventContent(privateRoom, 'm.room.pinned_events', '')).pinned, [welcome.event_id]);
  const ask = (txn, body) => sendEncrypted(client, privateRoom, txn, { msgtype: 'm.text', body });
  const setup = await ask('fixture-helper-setup', 'How do I set up Element Desktop?');
  const answer = await until(() => byBody('Click Edit'), 'the decrypted setup answer');
  assert.match(answer.content.body, /setup-guide\.md/u);
  assert.equal(answer.content['m.relates_to'].event_id, setup);
  const status = await ask('fixture-helper-health', 'Is Context Intelligence healthy?');
  const health = await until(() => byBody('stale snapshot'), 'the decrypted health answer');
  assert.equal(health.content['m.relates_to'].event_id, status);
  assert.match(turns[0], /isolated-revision/u);
  assert.match(turns[1], /Stale.*Context Intelligence.*healthy/u);
  assert.equal(turns.length, 2);
  console.log('Helper posted one decryptable welcome pin and answered setup and stale-health questions in separate encrypted threads.');
} finally {
  helper.kill();
  await once(helper, 'exit');
  client.stop();
  for (const socket of sockets) socket.terminate();
  acp.close();
  https.close();
}
