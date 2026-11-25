import assert from 'node:assert/strict';
import { execFileSync, spawn } from 'node:child_process';
import { once } from 'node:events';
import { mkdtemp, mkdir, readFile, stat, writeFile } from 'node:fs/promises';
import { createServer as createHttpServer, request as http } from 'node:http';
import { createServer } from 'node:https';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { setTimeout as delay } from 'node:timers/promises';
import { WebSocketServer } from 'ws';
import matrix from '@vector-im/matrix-bot-sdk';
import { openRoomClient, sendEncrypted } from './matrix-session.mjs';
import { mentionMessage } from './matrix-format.mjs';
import { createRosterApproval } from './chief-of-staff/direct/roster-approval.mjs';

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
  return child;
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

async function readState(path) {
  try {
    return JSON.parse(await readFile(path, 'utf8'));
  } catch (error) {
    // The SDK can briefly truncate its sync file while writing a new token.
    if (error.code !== 'ENOENT' && !(error instanceof SyntaxError)) throw error;
    return {};
  }
}

async function rosterFixture(chiefRoom, catalog, apply) {
  const db = new DatabaseSync(':memory:');
  const chiefId = '@chief-of-staff:' + domain;
  async function participant(key, userId, deviceId, name) {
    const state = join(base, deviceId);
    await mkdir(state);
    await writeFile(join(state, 'matrix-token'), await login(key, deviceId));
    const client = await openRoomClient({ url, state, roomId: chiefRoom, userId, deviceId, name });
    client.syncingTimeout = 0;
    let cursor;
    return { client, sync: async () => {
      const update = await client.doSync(cursor);
      cursor = update.next_batch;
      await client.processSync(update);
    } };
  }
  const { client: chief, sync: syncChief } = await participant('chief-of-staff', chiefId, 'ROSTERCHIEF', 'Chief of Staff');
  const { client: operator, sync: syncOperator } = await participant('operator', owner, 'ROSTEROPERATOR', 'Fixture Operator');

  async function receive(client, sync, event) {
    return eventually(async () => {
      await sync();
      const envelope = await client.getRawEvent(chiefRoom, event);
      assert.equal(envelope.type, 'm.room.encrypted');
      try {
        const decoded = await client.crypto.decryptRoomEvent(new matrix.EncryptedRoomEvent(envelope), chiefRoom);
        return { envelope, clear: decoded.raw };
      } catch (error) {
        if (error.code !== 'GenericFailure') throw error;
      }
    });
  }

  const profile = { key: 'fixture-worker', name: 'Fixture Worker', model: 'fixture-model', effort: 'low',
    profile: { role: 'worker', limits: 'Write welcome tips.', capabilities: [
      { code: 'welcome-tip', description: 'Write welcome tips.', resources: ['task:*'] },
    ] } };
  let time = Date.now();
  const approval = createRosterApproval({ db, client: chief, room: chiefRoom, owner,
    now: () => time, checkDestination: async () => assert.equal((await chief.getJoinedRoomMembers(chiefRoom)).length, 2),
    currentAgents: () => [{ ...catalog, placement: 'local' }], draft: async () => profile, apply });
  try {
    await syncChief();
    await syncOperator();
    for (const [command, expected] of [['hire fixture-worker Write welcome tips', 'add'], ['retire analyst', 'retire']]) {
      const event = await sendEncrypted(operator, chiefRoom, 'roster-command-' + expected, mentionMessage(command));
      const request = await receive(chief, syncChief, event);
      await approval.accept(request.clear, request.envelope);
      time = Date.now();
      await approval.tick();
      const row = db.prepare('SELECT * FROM roster_requests WHERE request=?').get(event);
      assert.ok(row?.poll);
      const proposal = await receive(operator, syncOperator, row.proposal);
      assert.equal(proposal.envelope.sender, chiefId);
      assert.match(proposal.clear.content.body, expected === 'add' ? /"key": "fixture-worker"/ : /Finish accepted assignments/);
      const poll = await receive(operator, syncOperator, row.poll);
      const start = 'org.matrix.msc3381.poll.start';
      const type = 'org.matrix.msc3381.poll.response';
      const answer = poll.clear.content[start].answers[0].id;
      const encrypted = await operator.crypto.encryptRoomEvent(chiefRoom, type, {
        [type]: { answers: [answer] }, 'm.relates_to': { rel_type: 'm.reference', event_id: row.poll },
      });
      const endpoint = '/_matrix/client/v3/rooms/' + encodeURIComponent(chiefRoom) + '/send/m.room.encrypted/roster-vote-' + expected;
      const vote = await operator.doRequest('PUT', endpoint, null, encrypted);
      const received = await receive(chief, syncChief, vote.event_id);
      assert.equal(received.envelope.sender, owner);
      assert.equal(received.envelope.content['m.relates_to'].event_id, row.poll);
      await approval.accept(received.clear, received.envelope);
      time = Date.now();
      await approval.tick();
      if (expected === 'retire') {
        assert.equal(db.prepare('SELECT announced FROM roster_requests WHERE request=?').get(event).announced, 'draining');
        time += 31_000;
        await approval.tick();
      }
      assert.equal(db.prepare('SELECT done FROM roster_requests WHERE request=?').get(event).done, 1);
    }
  } finally {
    operator.crypto.engine.machine.close();
    chief.crypto.engine.machine.close();
    db.close();
  }
}

async function welcomeFixture(office, chiefRoom, ownerToken, pins, tool, catalog) {
  const readerState = join(base, 'welcome-reader');
  await mkdir(readerState);
  await writeFile(join(readerState, 'matrix-token'), ownerToken);
  const reader = await openRoomClient({ url, state: readerState, roomId: office, userId: owner,
    deviceId: 'PINREAD', name: 'Fixture Operator' });
  const messages = [];
  reader.on('room.message', (roomId, event) => {
    if (roomId === office || roomId === chiefRoom) messages.push({ ...event, room_id: roomId });
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
  const records = [];
  const context = createHttpServer(async (request, response) => {
    assert.equal(request.url, '/events');
    assert.equal(request.headers.authorization, 'Bearer office-evidence-fixture');
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    records.push(JSON.parse(Buffer.concat(chunks)));
    response.writeHead(200).end();
  });
  await writeFile('/context-token', 'office-evidence-fixture');
  const analyst = { id: '@analyst:' + domain, key: 'analyst', name: 'Analyst', role: 'worker', limits: 'Fixture',
    status: 'Available', capabilities: [{ code: 'welcome-tip', description: 'Write one welcome tip.', resources: ['task:*'] }] };
  const manager = { id: '@manager:' + domain, key: 'manager', name: 'Manager', role: 'coordinator',
    limits: 'Fixture', status: 'Available', capabilities: [] };
  const handoff = { agent: 'analyst', capability: 'welcome-tip', resource: 'task:welcome' };
  let turn = 0;
  let releaseRetire;
  const retirementWork = new Promise(resolve => { releaseRetire = resolve; });
  ws.on('connection', (socket, upgrade) => {
    assert.equal(upgrade.headers.authorization, 'Bearer welcome-acp-fixture');
    socket.on('message', async raw => {
      const { method, id, params } = JSON.parse(raw);
      let result = {};
      if (method === 'initialize') result = { protocolVersion: 1, agentCapabilities: { loadSession: true } };
      if (method === 'session/new') result = { sessionId: 'welcome-' + ++turn };
      if (method === 'session/prompt') {
        const prompt = params.prompt.map(entry => entry.text ?? '').join('\n');
        assert.match(prompt, /three-step checklist/i);
        if (prompt.includes('Retirement fixture')) await retirementWork;
        const citedReply = /(?:^|\n)Analyst \((\$[^\s)]+)\):\n[^\n]*Fixture tip/u.exec(prompt)?.[1];
        const text = prompt.includes('Unverified fixture') ? 'TASK_OUTCOME: ' + JSON.stringify({status: 'completed',
          result: 'Checklist drafted', verification: 'Claimed an unverified check', evidence: ['$missing-receipt']})
          : prompt.includes('You are Analyst') ? 'Fixture tip: mention an available agent.'
          : citedReply ? 'TASK_OUTCOME: ' + JSON.stringify({status: 'completed', result: 'Welcome to Office',
            verification: 'Analyst shared a tip', evidence: [citedReply]})
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
  context.listen(0, '127.0.0.1');
  await once(context, 'listening');
  try {
    await writeFile(catalog, JSON.stringify({ version: 1, time: Math.floor(Date.now() / 1000),
      dynamicHandoffs: true, agents: [manager, analyst] }));
    for (const agent of [manager, analyst]) {
      const agentState = join(base, 'welcome-' + agent.key);
      await mkdir(agentState);
      await writeFile(join(agentState, 'room-id'), office);
      await writeFile(join(agentState, 'matrix-password'), password);
      launch('/app/connector.mjs', { AGENT_STATE: agentState, AGENT_WORKSPACE: base, AGENT_NAME: agent.name,
        AGENT_USERNAME: agent.key, MATRIX_CATALOG_PATH: catalog,
        MATRIX_PLANNER: '@dot:' + domain,
        CONTEXT_INTELLIGENCE_URL: 'http://127.0.0.1:' + context.address().port,
        INSTANCE_ACP_URL: 'wss://localhost:' + server.address().port + '/api/v1/acp' });
    }
    await eventually(async () => {
      const queried = await api('/_matrix/client/v3/keys/query', { method: 'POST', token: ownerToken, body: {
        device_keys: { [manager.id]: ['MATRIXMANAGER'], [analyst.id]: ['MATRIXANALYST'] },
      } });
      return queried.device_keys[manager.id]?.MATRIXMANAGER && queried.device_keys[analyst.id]?.MATRIXANALYST;
    });
    async function publish(payload) {
      const child = spawn(process.execPath, ['/app/welcome.mjs'], { stdio: ['pipe', 'pipe', 'inherit'] });
      const chunks = [];
      child.stdout.on('data', chunk => chunks.push(chunk));
      child.stdin.end(JSON.stringify(payload));
      const [code] = await once(child, 'close');
      assert.equal(code, 0);
      return JSON.parse(Buffer.concat(chunks)).event;
    }
    for (const variant of [
      { key: 'chief-of-staff', name: 'Chief of Staff', room: chiefRoom, device: 'WELCOMECHIEF', example: manager, dot: true },
      { key: 'analyst', name: 'Analyst', room: office, device: 'WELCOMEOFFICE', example: analyst, dot: false },
    ]) {
      const sender = '@' + variant.key + ':' + domain;
      const token = await login(variant.key, variant.device);
      const payload = { ...variant, token, sender, transaction: 'fixture-welcome', pins };
      const event = await publish(payload);
      assert.equal(await publish(payload), event, 'A retry on the same device should return the original welcome event');
      await api('/_matrix/client/v3/logout', { method: 'POST', body: {}, token });
      const raw = await api('/_matrix/client/v3/rooms/' + encodeURIComponent(variant.room) + '/event/' +
        encodeURIComponent(event), { token: ownerToken });
      assert.equal(raw.type, 'm.room.encrypted');
      assert.equal(raw.sender, sender);
      const welcome = await eventually(() => findMessage(item => item.event_id === event));
      assert.equal(welcome.room_id, variant.room);
      assert.deepEqual(welcome.content['m.mentions']?.user_ids ?? [], []);
      assert.ok(welcome.content.body.includes('Hi! I’m ' + variant.name));
      assert.ok(welcome.content.body.includes('VPS Alerts → Pinned messages'));
      assert.equal(pins.dashboard, pins.context);
      assert.ok(welcome.content.formatted_body.includes('Open workspace shortcuts'));
      assert.equal(welcome.content.formatted_body.split(pins.dashboard).length - 1, 1);
      assert.ok(welcome.content.body.includes('choose @' + variant.example.name + ' from Element’s mention picker'));
      assert.equal(welcome.content.body.includes('Constellation Office plugin'), variant.dot);
      assert.equal(welcome.content.body.includes('Manager is the entry point for coordinated Office tasks'), variant.dot);
      if (variant.dot) assert.ok(welcome.content.body.includes('Dot, send this task to Office starting with Manager'));
    }
    assert.equal(turn, 0);

    const task = 'Coordinate a three-step checklist for getting started in Office and post the result in this thread.';
    const fromElement = await sendEncrypted(reader, office, 'fixture-first-element-task', mentionMessage(task, manager));
    const { result } = await tool('publish_plan', {
      task_id: 'fixture-first-dot-task', title: 'Getting started in Office', plan: task, first_agent: 'manager',
    });
    assert.notEqual(result.isError, true, JSON.stringify(result));
    const fromDot = decodeURIComponent(new URL(result.structuredContent.thread_url).hash.split('/').at(-1));
    for (const event of [fromElement, fromDot]) {
      const submitted = await eventually(() => findMessage(item => item.event_id === event));
      assert.equal(submitted.room_id, office);
      assert.deepEqual(submitted.content['m.mentions'].user_ids, [manager.id]);
      const inThread = item => item.content['m.relates_to']?.event_id === event;
      const tip = await eventually(() => findMessage(item => inThread(item) && item.sender === analyst.id &&
        item.content.body.includes('Fixture tip')));
      const final = await eventually(() => findMessage(item => inThread(item) && item.sender === manager.id &&
        item.content['net.matrix_agents.task_outcome']?.status === 'completed'));
      const proof = await eventually(() => records.find(record => record.data.evidence.reply === final.event_id));
      assert.deepEqual(proof.data.evidence.references, [tip.event_id]);
      const history = records.filter(record => record.data.execution.task_id === proof.data.execution.task_id);
      assert.ok(history.some(record => record.event === 'agents:assignment_accepted' &&
        record.data.evidence.actor === analyst.id && record.data.evidence.from === manager.id));
      assert.notEqual(history.find(record => record.data.evidence.actor === analyst.id).data.session_id, proof.data.session_id);
    }
    const unverified = await sendEncrypted(reader, office, 'fixture-unverified',
      mentionMessage('Unverified fixture. Draft a three-step checklist.', manager));
    const progress = await eventually(() => findMessage(item => item.content['m.relates_to']?.event_id === unverified &&
      item.sender === manager.id));
    assert.match(progress.content.body, /no verifiable Office evidence/u);
    assert.equal(progress.content['net.matrix_agents.task_outcome'], undefined);
    const attempt = await eventually(() => records.find(record => record.data.evidence.reply === progress.event_id));
    assert.equal(attempt.data.evidence.outcome, 'progress');
    assert.equal(attempt.data.evidence.references, undefined);
    const duringRetirement = await sendEncrypted(reader, office, 'retirement-fixture-active',
      mentionMessage('Retirement fixture: draft a three-step checklist.', analyst));
    const workerDb = new DatabaseSync(join(base, 'welcome-analyst', 'connector.sqlite3'));
    workerDb.exec('PRAGMA busy_timeout=3000');
    try {
      await eventually(() => workerDb.prepare('SELECT id FROM inbox WHERE id=? AND sent IS NULL').get(duringRetirement));
      let draining = false;
      await rosterFixture(chiefRoom, analyst, async payload => {
        const operation = JSON.parse(payload.operation);
        if (operation.kind === 'add') {
          assert.equal(operation.profile.key, 'fixture-worker');
          return { status: 'provisioned' };
        }
        assert.equal(operation.agent, 'analyst');
        if (!draining) {
          workerDb.exec(await readFile('/app/retire-worker.sql', 'utf8'));
          const ignored = await sendEncrypted(reader, office, 'retirement-fixture-blocked',
            mentionMessage('Try to assign another three-step checklist.', analyst));
          releaseRetire();
          await eventually(() => findMessage(item => item.content['m.relates_to']?.event_id === duringRetirement &&
            item.sender === analyst.id));
          assert.equal(workerDb.prepare('SELECT id FROM inbox WHERE id=?').get(ignored), undefined);
          draining = true;
          return { status: 'draining' };
        }
        return { status: 'retired' };
      });
    } finally {
      releaseRetire();
      workerDb.close();
    }
    console.log('Real encrypted Chief of Staff approvals verified. A retiring worker delivered its accepted task and rejected new work.');
    const identity = await api('/_matrix/client/v3/profile/' + encodeURIComponent(owner), { token: ownerToken });
    assert.equal(identity.displayname, 'Fixture Operator');
    console.log('Chief and Office welcomes stayed informational. Element and Dot both routed the example to Manager.');
  } finally {
    reader.crypto.engine.machine.close();
    for (const connection of ws.clients) connection.terminate();
    ws.close();
    await new Promise(resolve => server.close(resolve));
    await new Promise(resolve => context.close(resolve));
  }
}

try {
  const alerts = await room('vps-alerts');
  const office = await room('office');
  const telegramControl = await room('telegram-control');
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
  const monitorId = '@monitor:' + domain;
  const monitorToken = await login('monitor', 'VPSMONITOR');
  await writeFile(join(monitor, 'token'), monitorToken);
  await writeFile(join(monitor, 'matrix-token'), monitorToken);
  const legacy = await openRoomClient({ url, state: monitor, roomId: alerts, userId: monitorId,
    deviceId: 'VPSMONITOR', name: 'Monitor', token: monitorToken });
  const entries = {};
  for (const [key, label] of [['dashboard', 'Open dashboard'], ['context', 'Open Context Intelligence']]) {
    const body = `${label} — ${links[key]}`;
    const event = await sendEncrypted(legacy, alerts, `fixture-legacy-${key}`, { msgtype: 'm.text', body });
    entries[key] = { body, event };
  }
  legacy.crypto.engine.machine.close();
  const oldPins = [preexisting.event_id, ...Object.values(entries).map(({ event }) => event)];
  const monitorEnv = { STATE_DIR: monitor, DATA_DIR: monitorFiles, WORKSPACE_LINKS: join(base, 'links.json'),
    DOT_ENABLED: 'true', SYSTEMD_ENABLED: 'true', GUIDE_COMMAND: 'sudo ./constellation_matrix',
    GRAPH_PASSWORD_COMMAND: "sudo cat -- '/srv/fixture workspace/context/neo4j-password'" };
  const freshMonitor = launch('/app/ops/monitor.mjs', monitorEnv);
  await eventually(async () => (await readState(join(monitor, 'sync.json'))).syncToken);
  assert.deepEqual((await api(pinState, { token })).pinned, [preexisting.event_id]);
  const telegramGuide = join(base, 'telegram-guide');
  await mkdir(telegramGuide);
  // The layout fixture created the rooms and device with no Telegram credentials or Amplifier session.
  const telegramAgentToken = await login('telegram-agent', 'TELEGRAM_SETUP');
  await writeFile(join(telegramGuide, 'token'), telegramAgentToken);
  await writeFile(join(telegramGuide, 'room-id'), telegramControl);
  await writeFile(join(telegramGuide, 'command'), 'sudo ./constellation_matrix');
  launch('/app/ops/monitor.mjs', { STATE_DIR: telegramGuide, TELEGRAM_GUIDE: 'true' });
  await eventually(async () => (await readState(join(telegramGuide, 'sync.json'))).syncToken);
  const telegramPins = state(telegramControl, 'm.room.pinned_events');
  await assert.rejects(api(telegramPins, { token }), /HTTP 404/);

  const pinReaderState = join(base, 'pin-reader');
  await mkdir(pinReaderState);
  const pinReaderToken = await login('operator', 'PINDEVICE');
  await writeFile(join(pinReaderState, 'matrix-token'), pinReaderToken);
  const pinReader = await openRoomClient({ url, state: pinReaderState, roomId: alerts, userId: owner,
    deviceId: 'PINDEVICE', name: 'Fixture Operator', token: pinReaderToken });
  pinReader.syncingTimeout = 0;
  let cursor;
  async function readShortcut(event, target = alerts) {
    const batch = await pinReader.doSync(cursor);
    cursor = batch.next_batch;
    await pinReader.crypto.updateSyncData(batch.to_device?.events || [], batch.device_one_time_keys_count || {},
      batch.device_unused_fallback_key_types || [], batch.device_lists?.changed || [], batch.device_lists?.left || []);
    const raw = await pinReader.getRawEvent(target, event);
    assert.equal(raw.type, 'm.room.encrypted');
    try {
      return (await pinReader.crypto.decryptRoomEvent(new matrix.EncryptedRoomEvent(raw), target)).raw.content;
    } catch (error) {
      if (error.code !== 'GenericFailure') throw error;
      return null;
    }
  }
  async function managedShortcut(exclude) {
    return eventually(async () => {
      const { pinned } = await api(pinState, { token });
      const { entries = {} } = await readState(join(monitor, 'shortcut-pins.json'));
      const event = entries.dashboard?.event;
      return pinned.length === 3 && pinned[0] === preexisting.event_id && pinned.includes(event) &&
        pinned.includes(entries['dot-setup']?.event) && !exclude.includes(event) && event;
    });
  }
  async function verifyShortcut(event, expected) {
    const content = await eventually(() => readShortcut(event));
    assert.match(content.body, /^Workspace shortcuts\n/u);
    assert.ok(content.formatted_body.includes(`<a href="${expected.dashboard}">Open dashboard</a>`));
    assert.ok(content.formatted_body.includes(`<a href="${expected.context}">Open Context Intelligence</a>`));
    assert.ok(content.body.includes(monitorEnv.GRAPH_PASSWORD_COMMAND));
  }
  const fresh = await managedShortcut(oldPins);
  await verifyShortcut(fresh, links);
  const { entries: freshPins } = JSON.parse(await readFile(join(monitor, 'shortcut-pins.json'), 'utf8'));
  const dotSetup = await eventually(() => readShortcut(freshPins['dot-setup'].event));
  assert.match(dotSetup.body, /^Connect Dot\n/u);
  assert.match(dotSetup.body, /list_agents/u);
  const telegramSetup = await eventually(async () => (await api(telegramPins, { token }).catch(error => {
    if (!error.message.endsWith('HTTP 404')) throw error;
    return { pinned: [] };
  })).pinned[0]);
  const telegramInstructions = await eventually(() => readShortcut(telegramSetup, telegramControl));
  assert.match(telegramInstructions.body, /^Set up Telegram\n/u);
  assert.match(telegramInstructions.body, /Approve and send/u);
  assert.match(telegramInstructions.body, /REPLACE_WITH_YOUR_API_HASH/u);
  console.log('A fresh VPS Alerts installation waited for operator keys and published one readable pin with both links.');

  assert.equal(await readShortcut(oldPins[1]), null, 'A new device cannot decrypt the original shortcut');
  const stopped = once(freshMonitor, 'exit');
  freshMonitor.kill();
  await stopped;
  children.splice(children.indexOf(freshMonitor), 1);
  await api(pinState, { method: 'PUT', token, body: { pinned: oldPins } });
  await writeFile(join(monitor, 'shortcut-pins.json'), JSON.stringify({ entries, replaced: [] }));
  launch('/app/ops/monitor.mjs', monitorEnv);
  const first = await managedShortcut([...oldPins, fresh]);
  await verifyShortcut(first, links);
  const updated = { ...links, dashboard: 'https://fixture.test:9443/' };
  await writeFile(join(base, 'links.json'), JSON.stringify(updated));
  const changed = await managedShortcut([first]);
  await verifyShortcut(changed, updated);
  pinReader.crypto.engine.machine.close();
  console.log('VPS Alerts replaced both legacy pins with one readable shortcut and retained the operator pin.');

  const dot = join(base, 'dot');
  const bridgeSecret = 'synthetic-private-bridge-token-0123456789';
  await mkdir(dot);
  await writeFile(join(dot, 'room-id'), office);
  await writeFile(join(dot, 'matrix-password'), password);
  await writeFile(join(dot, 'bridge-token'), bridgeSecret);
  const catalog = join(base, 'agents.json');
  const snapshot = { version: 1, time: Math.floor(Date.now() / 1000), dynamicHandoffs: true, agents: [] };
  await writeFile(catalog, JSON.stringify(snapshot));
  launch('/app/dot-bridge.mjs', { DOT_STATE: dot, MATRIX_CATALOG_PATH: catalog });
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
  const empty = await eventually(async () => {
    const { result } = await tool('list_agents', {});
    return result?.isError === true ? null : result?.structuredContent;
  });
  assert.deepEqual(empty, { agents: [] });
  snapshot.agents.push({ key: 'analyst', id: '@analyst:' + domain, name: 'Analyst', role: 'worker',
    limits: 'Fixture', status: 'Available', capabilities: [] });
  await writeFile(catalog, JSON.stringify(snapshot));
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
  const { entries: managedPins } = JSON.parse(await readFile(join(monitor, 'shortcut-pins.json'), 'utf8'));
  const pinLinks = Object.fromEntries(Object.entries(managedPins).map(([key, pin]) => [key,
    'https://matrix.to/#/' + encodeURIComponent(alerts) + '/' + encodeURIComponent(pin.event)]));
  await welcomeFixture(office, await room('chief-of-staff-dm'), token, pinLinks, tool, catalog);
} finally {
  for (const child of children) child.kill();
}
