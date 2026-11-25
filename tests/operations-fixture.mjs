import assert from 'node:assert/strict';
import { execFileSync, spawn } from 'node:child_process';
import { once } from 'node:events';
import { mkdtemp, mkdir, readFile, stat, writeFile } from 'node:fs/promises';
import { request as http } from 'node:http';
import { createServer } from 'node:https';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { WebSocketServer } from 'ws';
import { openRoomClient } from './matrix-session.mjs';

const url = 'http://synapse:8008';
const domain = 'fixture.test';
const owner = '@operator:' + domain;
const password = 'synthetic-test-passphrase-88';
const children = [];
const base = await mkdtemp('/tmp/constellation-operations-');
const links = { dashboard: 'https://fixture.test:8443/', context: 'https://fixture.test:8444/browser/' };

async function api(path, { method = 'GET', body, token } = {}) {
  const response = await fetch(url + path, {
    method, headers: { 'content-type': 'application/json', ...(token && { authorization: 'Bearer ' + token }) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  assert.ok(response.ok, `Matrix ${method} ${path} HTTP ${response.status}`);
  return response.json();
}

async function login(user, device) {
  return (await api('/_matrix/client/v3/login', { method: 'POST', body: {
    type: 'm.login.password', identifier: { type: 'm.id.user', user }, password, device_id: device,
  } })).access_token;
}

const room = async alias => (await api('/_matrix/client/v3/directory/room/' + encodeURIComponent(`#${alias}:${domain}`))).room_id;
const state = (id, suffix) => '/_matrix/client/v3/rooms/' + encodeURIComponent(id) + '/state/' + suffix;
const environment = { ...process.env, MATRIX_URL: url, MATRIX_SERVER_NAME: domain, MATRIX_OWNER: owner };
function launch(script, env) {
  const child = spawn(process.execPath, [script], { env: { ...environment, ...env }, stdio: 'inherit' });
  children.push(child);
}

async function eventually(check) {
  for (let i = 0; i < 70; i++) {
    const result = await check();
    if (result) return result;
    assert.ok(children.every(child => child.exitCode === null), 'An operations service exited early');
    await delay(500);
  }
  assert.fail('The operations service did not become ready');
}

async function welcomeFixture(office, ownerToken) {
  const readerState = join(base, 'welcome-reader');
  await mkdir(readerState);
  await writeFile(join(readerState, 'matrix-token'), ownerToken);
  const reader = await openRoomClient({ url, state: readerState, roomId: office, userId: owner,
    deviceId: 'PINREAD', name: 'Fixture Operator' });
  const messages = [];
  reader.on('room.message', (roomId, event) => {
    if (roomId === office) messages.push(event);
  });
  reader.syncingTimeout = 0;
  let nextBatch;
  async function findMessage(predicate) {
    const update = await reader.doSync(nextBatch);
    nextBatch = update.next_batch;
    await reader.processSync(update);
    return messages.find(predicate);
  }

  await mkdir('/acp');
  const ca = '/acp/ca.pem';
  const key = '/acp/key.pem';
  execFileSync('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-subj', '/CN=localhost',
    '-addext', 'subjectAltName=DNS:localhost', '-keyout', key, '-out', ca, '-days', '1'], { stdio: 'ignore' });
  await writeFile('/acp/credential-token', 'welcome-acp-fixture');
  const server = createServer({ key: await readFile(key), cert: await readFile(ca) });
  const ws = new WebSocketServer({ server, path: '/api/v1/acp' });
  const analyst = { id: '@analyst:' + domain, key: 'analyst', name: 'Analyst', role: 'worker', limits: 'Fixture',
    status: 'Available', capabilities: [{ code: 'welcome-tip', description: 'Write one welcome tip.', resources: ['task:*'] }] };
  const manager = { id: '@manager:' + domain, key: 'manager', name: 'Manager', role: 'coordinator',
    limits: 'Fixture', status: 'Available', capabilities: [] };
  const handoff = { agent: 'analyst', capability: 'welcome-tip', resource: 'task:welcome' };
  let turn = 0;
  ws.on('connection', (socket, upgrade) => {
    assert.equal(upgrade.headers.authorization, 'Bearer welcome-acp-fixture');
    socket.on('message', raw => {
      const { method, id, params } = JSON.parse(raw);
      let result = {};
      if (method === 'initialize') result = { protocolVersion: 1, agentCapabilities: { loadSession: true } };
      if (method === 'session/new') result = { sessionId: 'welcome-' + ++turn };
      if (method === 'session/prompt') {
        const prompt = params.prompt.map(entry => entry.text ?? '').join('\n');
        assert.match(prompt, /Hello Collaboration/);
        const text = prompt.includes('You are Analyst') ? 'Fixture tip: mention an available agent.'
          : prompt.includes('Fixture tip: mention an available agent.') ?
            'TASK_OUTCOME: {"status":"completed","result":"Welcome to Office","verification":"Analyst shared a tip"}'
            : 'Analyst, add a welcome tip.\nHANDOFF: ' + JSON.stringify(handoff);
        socket.send(JSON.stringify({ jsonrpc: '2.0', method: 'session/update', params: {
          sessionId: params.sessionId, update: { sessionUpdate: 'agent_message_chunk', content: { type: 'text', text } },
        } }));
        result.stopReason = 'end_turn';
      }
      if (id !== undefined) socket.send(JSON.stringify({ jsonrpc: '2.0', id, result }));
    });
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  try {
    const catalog = join(base, 'welcome-roster.json');
    await writeFile(catalog, JSON.stringify({ version: 1, time: Math.floor(Date.now() / 1000),
      dynamicHandoffs: true, agents: [manager, analyst] }));
    for (const agent of [manager, analyst]) {
      const agentState = join(base, 'welcome-' + agent.key);
      await mkdir(agentState);
      await writeFile(join(agentState, 'room-id'), office);
      await writeFile(join(agentState, 'matrix-password'), password);
      launch('/app/connector.mjs', { AGENT_STATE: agentState, AGENT_WORKSPACE: base, AGENT_NAME: agent.name,
        AGENT_USERNAME: agent.key, MATRIX_CATALOG_PATH: catalog,
        INSTANCE_ACP_URL: 'wss://localhost:' + server.address().port + '/api/v1/acp' });
    }
    await eventually(async () => {
      const queried = await api('/_matrix/client/v3/keys/query', { method: 'POST', token: ownerToken, body: {
        device_keys: { [manager.id]: ['MATRIXMANAGER'], [analyst.id]: ['MATRIXANALYST'] },
      } });
      return queried.device_keys[manager.id]?.MATRIXMANAGER && queried.device_keys[analyst.id]?.MATRIXANALYST;
    });
    const device = 'CONSTELLATIONWELCOME';
    const welcomeToken = await login('operator', device);
    const payload = { token: welcomeToken, owner, device, room: office, transaction: 'fixture-welcome',
      target: manager, peer: analyst };
    async function publish() {
      const child = spawn(process.execPath, ['/app/welcome.mjs'], { stdio: ['pipe', 'pipe', 'inherit'] });
      const chunks = [];
      child.stdout.on('data', chunk => chunks.push(chunk));
      child.stdin.end(JSON.stringify(payload));
      const [code] = await once(child, 'close');
      assert.equal(code, 0);
      return JSON.parse(Buffer.concat(chunks)).event;
    }
    const event = await publish();
    assert.equal(await publish(), event, 'A retry on the same device should return the original welcome event');
    const raw = await api('/_matrix/client/v3/rooms/' + encodeURIComponent(office) + '/event/' + encodeURIComponent(event),
      { token: ownerToken });
    assert.equal(raw.type, 'm.room.encrypted');
    assert.equal(raw.sender, owner);
    const welcome = await eventually(() => findMessage(item => item.event_id === event));
    assert.equal(welcome.sender, owner);
    assert.deepEqual(welcome.content['m.mentions'].user_ids, [manager.id]);
    assert.match(welcome.content.body, /Hello Collaboration/);
    const inThread = item => item.content['m.relates_to']?.event_id === event;
    await eventually(() => findMessage(item => inThread(item) && item.sender === analyst.id &&
      item.content.body.includes('Fixture tip')));
    await eventually(() => findMessage(item => inThread(item) && item.sender === manager.id &&
      item.content['net.matrix_agents.task_outcome']?.status === 'completed'));
    const identity = await api('/_matrix/client/v3/profile/' + encodeURIComponent(owner), { token: ownerToken });
    assert.equal(identity.displayname, 'Fixture Operator');
    console.log('Hello Collaboration sent once as encrypted Office text, delegated to Analyst, and closed by Manager.');
  } finally {
    reader.crypto.engine.machine.close();
    for (const connection of ws.clients) connection.terminate();
    ws.close();
    await new Promise(resolve => server.close(resolve));
  }
}

try {
  const alerts = await room('vps-alerts');
  const office = await room('office');
  const token = await login('operator', 'PINREAD');
  const preexisting = await api('/_matrix/client/v3/rooms/' + encodeURIComponent(alerts) + '/send/m.room.message/operator-pin',
    { method: 'PUT', token, body: { msgtype: 'm.text', body: 'Existing fixture pin' } });
  const pinState = state(alerts, 'm.room.pinned_events');
  await api(pinState, { method: 'PUT', token, body: { pinned: [preexisting.event_id] } });
  const monitor = join(base, 'bot');
  const monitorFiles = join(base, 'outbox');
  await mkdir(monitor);
  await mkdir(join(monitorFiles, 'outbox'), { recursive: true });
  await mkdir(join(monitorFiles, 'acks'));
  await writeFile(join(base, 'links.json'), JSON.stringify(links));
  await writeFile(join(monitor, 'password'), password);
  await writeFile(join(monitor, 'room-id'), alerts);
  launch('/app/ops/monitor.mjs', { STATE_DIR: monitor, DATA_DIR: monitorFiles, WORKSPACE_LINKS: join(base, 'links.json') });
  const first = await eventually(async () => {
    const pins = await api(pinState, { token });
    return pins.pinned.length === 3 && pins.pinned;
  });
  assert.equal(first[0], preexisting.event_id);
  for (const event of first.slice(1)) {
    const raw = await api('/_matrix/client/v3/rooms/' + encodeURIComponent(alerts) + '/event/' + encodeURIComponent(event), { token });
    assert.equal(raw.type, 'm.room.encrypted');
  }
  await writeFile(join(base, 'links.json'), JSON.stringify({ ...links, dashboard: 'https://fixture.test:9443/' }));
  const changed = await eventually(async () => {
    const pins = await api(pinState, { token });
    return pins.pinned.some(item => !first.includes(item)) && pins.pinned;
  });
  assert.deepEqual(changed.slice(0, 2), [first[0], first[2]]);
  console.log('VPS Alerts published actual encrypted shortcut pins and retained the operator pin.');

  const dot = join(base, 'dot');
  const bridgeSecret = 'synthetic-private-bridge-token-0123456789';
  await mkdir(dot);
  await writeFile(join(dot, 'room-id'), office);
  await writeFile(join(dot, 'matrix-password'), password);
  await writeFile(join(dot, 'bridge-token'), bridgeSecret);
  await writeFile(join(base, 'agents.json'), JSON.stringify({ version: 1, time: Math.floor(Date.now() / 1000),
    dynamicHandoffs: true, agents: [{ key: 'analyst', id: '@analyst:' + domain, name: 'Analyst', role: 'worker',
      limits: 'Fixture', status: 'Available', capabilities: [] }] }));
  launch('/app/dot-bridge.mjs', { DOT_STATE: dot, MATRIX_CATALOG_PATH: join(base, 'agents.json') });
  const socketPath = join(dot, 'bridge.sock');
  await eventually(async () => (await stat(socketPath).catch(() => null))?.isSocket());
  let id = 0;
  function tool(name, args, bridgeToken = bridgeSecret) {
    return new Promise((resolve, reject) => {
      const req = http({ socketPath, method: 'POST', path: '/mcp', headers: {
        'x-dot-bridge-token': bridgeToken, 'content-type': 'application/json', accept: 'application/json, text/event-stream',
      } }, res => {
        let body = '';
        res.setEncoding('utf8');
        res.on('data', part => { body += part; });
        res.on('end', () => resolve({ status: res.statusCode, result: body && JSON.parse(body).result }));
      });
      req.on('error', reject);
      req.end(JSON.stringify({ jsonrpc: '2.0', id: ++id, method: 'tools/call', params: { name, arguments: args } }));
    });
  }
  assert.equal((await tool('list_agents', {}, 'wrong-token')).status, 401);
  const ownTask = { task_id: 'fixture-task-01', title: 'Fixture plan', plan: 'Report the fixture result', first_agent: 'analyst' };
  const published = await eventually(async () => {
    const { result } = await tool('publish_plan', ownTask);
    return result?.content && result.isError !== true && result;
  });
  const duplicate = await tool('publish_plan', ownTask);
  assert.deepEqual(duplicate.result, published);
  assert.notEqual((await tool('get_task', { task_id: ownTask.task_id })).result.isError, true);
  const foreign = await tool('get_task', { task_id: 'unpublished-task-01' });
  assert.equal(foreign.result.isError, true);
  assert.match(foreign.result.content[0].text, /No task with that ID/);
  console.log('Dot reused one encrypted task and rejected an unpublished task ID.');
  await welcomeFixture(office, token);
} finally {
  for (const child of children) child.kill();
}
