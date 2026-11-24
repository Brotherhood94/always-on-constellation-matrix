import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, mkdir, stat, writeFile } from 'node:fs/promises';
import { request as http } from 'node:http';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';

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
  for (let i = 0; i < 35; i++) {
    const result = await check();
    if (result) return result;
    assert.ok(children.every(child => child.exitCode === null), 'An operations service exited early');
    await delay(500);
  }
  assert.fail('The operations service did not become ready');
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
} finally {
  for (const child of children) child.kill();
}
