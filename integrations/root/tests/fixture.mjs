import { mentionMessage, turnKey } from '../../agents/connector/matrix-routing.mjs';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdir, stat, writeFile } from 'node:fs/promises';
import { createServer } from 'node:net';
import { createServer as httpServer } from 'node:http';
import { once } from 'node:events';
import { DatabaseSync } from 'node:sqlite';
import { setTimeout as delay } from 'node:timers/promises';
import matrix from '@vector-im/matrix-bot-sdk';
import { openRoomClient, sendEncrypted, sendReaction } from './matrix-session.mjs';
import { digest, pollStart, pollText } from './protocol.mjs';

const url = 'http://synapse:8008';
async function api(path, body, token) {
  const response = await fetch(url + '/_matrix/client/v3' + path, {
    method: body ? 'POST' : 'GET', headers: {'content-type': 'application/json', ...(token ? {authorization: 'Bearer ' + token} : {})},
    body: body ? JSON.stringify(body) : undefined,
  });
  assert.ok(response.ok, path + ' ' + response.status + ' ' + (!response.ok ? await response.text() : ''));
  return response.json();
}
async function register(name, device) {
  return { ...await api('/register', {username: name, password: 'fixture-password', device_id: device, auth: {type:'m.login.dummy'}}), device };
}
const [root, owner, manager] = await Promise.all([register('vps-manager', 'VPSMANAGER'), register('operator', 'FIXTUREOWNER'), register('manager', 'FIXTUREMANAGER')]);
async function privateRoom(invite) {
  return (await api('/createRoom', {preset:'private_chat', visibility:'private', creation_content:{'m.federate':false},
    invite, initial_state:[
      {type:'m.room.encryption', state_key:'', content:{algorithm:'m.megolm.v1.aes-sha2'}},
      {type:'m.room.history_visibility', state_key:'', content:{history_visibility:'joined'}},
    ]}, root.access_token)).room_id;
}
const audit = await privateRoom([owner.user_id]);
const agents = await privateRoom([owner.user_id, manager.user_id]);
await api('/join/' + encodeURIComponent(audit), {}, owner.access_token);
await api('/join/' + encodeURIComponent(agents), {}, owner.access_token);
await api('/join/' + encodeURIComponent(agents), {}, manager.access_token);
await writeFile('/config.json', JSON.stringify({url, agents, audit, owner:owner.user_id, root:root.user_id}));
await writeFile('/context-token', 'fixture');
await writeFile('/office-token', 'office-fixture');
await writeFile('/state/agents.json', JSON.stringify({ version: 1, time: Math.floor(Date.now() / 1000),
  dynamicHandoffs: true, agents: [
    { key: 'manager', id: manager.user_id, name: 'Manager', role: 'coordinator', limits: 'Fixture', status: 'Available', capabilities: [] },
    { key: 'vps-manager', id: root.user_id, name: 'VPS Manager', role: 'worker', limits: 'Fixture', status: 'Available', capabilities: [] },
  ],
}));
await writeFile('/state/model-ready', 'ready');
await mkdir('/credentials');
await writeFile('/credentials/matrix-token', root.access_token);
await mkdir('/tmp/root-owner-fixture');
await writeFile('/tmp/root-owner-fixture/matrix-token', owner.access_token);
const reader = await openRoomClient({
  url, state:'/tmp/root-owner-fixture', userId:owner.user_id, deviceId:owner.device, roomId:audit, name:'Fixture Owner',
});
await mkdir('/tmp/dot-owner-fixture');
const managerClient = await openRoomClient({
  url, state:'/tmp/dot-owner-fixture', userId:manager.user_id, deviceId:manager.device, roomId:agents, name:'Manager', token:manager.access_token,
});

const archived = [];
const context = httpServer(async (request, response) => {
  assert.equal(request.url, '/events');
  assert.ok(['Bearer fixture', 'Bearer office-fixture'].includes(request.headers.authorization));
  let body = '';
  for await (const part of request) body += part;
  archived.push(JSON.parse(body));
  response.writeHead(200, {'content-type':'application/json'});
  response.end('{"status":"queued"}');
});
await new Promise(resolve => context.listen(8000, '0.0.0.0', resolve));
const accepted = [];
const maintenanceCalls = [];
const fixtureDiff = '--- a/dashboard/index.html\n+++ b/dashboard/index.html\n-Old dashboard\n+New dashboard\n';
const release = {base:'a'.repeat(40),revision:'b'.repeat(64),diff_sha256:digest(fixtureDiff),services:[],dashboard:true};
const reviewed = 'printf "%s\\n" "<candidate>"\nexit 41\n';
const sockets = createServer(socket => {
  let line = '';
  socket.on('data', data => {
    line += String(data);
    if (line.endsWith('\n')) {
      const value = JSON.parse(line);
      let result;
      if (value.maintenance) {
        if (value.maintenance !== 'scan') maintenanceCalls.push(value);
        if (value.maintenance === 'prepare') result = {workspace:value.id,base:release.base};
        else if (value.maintenance === 'seal') result = {release,diff:fixtureDiff,tests:'Isolated dashboard tests passed.'};
        else result = {receipts: accepted.some(item => item.operation === 'maintenance-release') ?
          [{id:'fixture-recovery',service:'coder',time:1780000000,reason:'unhealthy',status:'healthy'}] : []};
      } else {
        accepted.push(value);
        result = value.operation === 'maintenance-release' ? {outcome:'completed',result:{revision:release.revision}} :
          {outcome:value.action === 'approve' ? 'failed' : 'denied', exit_code:value.action === 'approve' ? 41 : null};
      }
      socket.end(JSON.stringify(result) + '\n');
    }
  });
});
await new Promise(resolve => sockets.listen('/run/executor.sock', resolve));
const worker = spawn('node', ['/app/root/agent.mjs'], {stdio: 'inherit', env: { ...process.env, MATRIX_CATALOG_PATH: '/state/agents.json', CONTEXT_INTELLIGENCE_URL: 'http://context:8000', VPS_MAINTENANCE:'true' }});
let database;

async function waitFor(lookup) {
  for (let attempt=0; attempt<45; attempt++) {
    const result = await lookup();
    if (result) return result;
    await delay(500);
  }
  throw Error('Timed out waiting for the fixture');
}

async function timeline(since) {
  const batch = await reader.doRequest('GET', '/_matrix/client/v3/sync', {...(since && {since}), timeout:0});
  await reader.crypto.updateSyncData(batch.to_device?.events || [], batch.device_one_time_keys_count || {},
    batch.device_unused_fallback_key_types || [], batch.device_lists?.changed || [], batch.device_lists?.left || []);
  return batch;
}

async function castVote(transaction, poll, answer) {
  const type = 'org.matrix.msc3381.poll.response';
  const content = await reader.crypto.encryptRoomEvent(audit, type, {
    'm.relates_to': { rel_type: 'm.reference', event_id: poll }, [type]: { answers: [answer] },
  });
  const endpoint = '/_matrix/client/v3/rooms/' + encodeURIComponent(audit) + '/send/m.room.encrypted/' + transaction;
  return (await reader.doRequest('PUT', endpoint, null, content, 15000)).event_id;
}

try {
  const readiness = await waitFor(async () => {
    try {
      return await stat('/state/synced');
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
      return null;
    }
  });
  assert.ok(readiness.size > 0, 'Root must write a non-empty marker for the Compose health check.');
  let cursor = (await timeline()).next_batch;
  const thread = await sendEncrypted(reader, agents, 'fixture-thread', mentionMessage('Original task thread', {id: manager.user_id, name: 'Manager'}));
  const forged = await sendEncrypted(managerClient, agents, 'fixture-forged', {
    ...mentionMessage('Check a synthetic local ID.', {id: root.user_id, name: 'VPS Manager'}),
    [turnKey]: {request: thread, number: 1},
    'm.relates_to': {rel_type: 'm.thread', event_id: thread, 'm.in_reply_to': {event_id: '$unrelated'}},
  });
  for (const [index, action, label] of [[1, 'approve', 'Accept'], [2, 'reject', 'Reject'], [3, 'approve', 'Accept']]) {
    const dynamic = index !== 3;
    const exact = index === 1;
    const draft = {operation:'reviewed-plan', summary:'Confirm exact submitted bytes.', impact:'Runs only the fixture script.',
      verification:'The fixture check must pass.', recovery:'Inspect the fixture state.', plan:{kind:'shell', script:reviewed, verify:'test -d /tmp'}};
    const body = exact ? 'Root Dot review request\n\nROOT_REVIEW: ' + JSON.stringify(draft)
      : dynamic ? 'Root: Check a synthetic local ID.' : 'Root: fixed status fixture';
    const sender = exact ? manager.user_id : owner.user_id;
    const source = await sendEncrypted(exact ? managerClient : reader, agents, 'fixture-request-' + index,
      {...mentionMessage(body, {id: root.user_id, name: 'VPS Manager'}),
        ...(index === 1 && {'m.relates_to':{rel_type:'m.thread',event_id:thread,is_falling_back:true,'m.in_reply_to':{event_id:thread}},
          [turnKey]: {request:thread, number:1, managed:{version:2,manager:manager.user_id,target:root.user_id}}})});
    const state = await waitFor(() => {
      database ||= new DatabaseSync('/state/gateway.sqlite3', {readOnly:true});
      const task = database.prepare('SELECT tasks.*, review_polls.event AS poll_event FROM tasks LEFT JOIN review_polls USING(id) WHERE tasks.event=?').get(source);
      assert.equal(task?.outcome ?? null, null, task?.summary);
      return task?.poll_event && task;
    });
    assert.equal(accepted.length, index - 1);
    let notice;
    let poll;
    await waitFor(async () => {
      const batch = await timeline(cursor);
      cursor = batch.next_batch;
      for (const raw of batch.rooms?.join?.[audit]?.timeline?.events ?? []) {
        if (raw.event_id === state.proposal_event) {
          assert.equal(raw.type, 'm.room.encrypted');
          notice = (await reader.crypto.decryptRoomEvent(new matrix.EncryptedRoomEvent(raw), audit)).raw;
        }
        if (raw.event_id === state.poll_event) {
          assert.equal(raw.type, 'm.room.encrypted');
          const decoded = (await reader.crypto.decryptRoomEvent(new matrix.EncryptedRoomEvent(raw), audit)).raw;
          assert.equal(decoded.type, pollStart);
          assert.ok(decoded.content[pollText].includes(encodeURIComponent(state.proposal_event)));
          assert.ok(decoded.content[pollText].includes(state.summary));
          poll = decoded.content[pollStart];
        }
      }
      return notice && poll;
    });
    assert.deepEqual(notice.content['m.mentions'], {user_ids:[owner.user_id]});
    if (dynamic) {
      assert.ok(notice.content.body.includes(reviewed), 'The proposal contains the script, including its trailing newline.');
      assert.ok(notice.content.formatted_body.includes('&lt;candidate&gt;'));
      assert.ok(notice.content.body.includes('Verify\ntest -d /tmp'));
    } else {
      assert.match(notice.content.body, /systemctl is-active --quiet matrix-stack.service/u);
    }
    assert.ok(notice.content.body.includes('Requested by · ' + sender));
    assert.ok(notice.content.body.includes('Impact · ' + (dynamic ? 'Runs only the fixture script.' : 'Reads the systemd state')));
    assert.match(notice.content.formatted_body, /<strong>Purpose<\/strong>/u);
    assert.equal(poll.max_selections, 1);
    assert.deepEqual(poll.answers.map(answer => answer[pollText]), ['Accept', 'Reject']);
    await sendReaction(reader, audit, 'fixture-legacy-' + index, state.proposal_event, '✅');
    await delay(1400);
    assert.equal(accepted.length, index - 1);
    const selected = await castVote('fixture-vote-' + index, state.poll_event, poll.answers.find(answer => answer[pollText] === label).id);
    const result = await waitFor(() => database.prepare('SELECT * FROM tasks WHERE id=? AND notified=1').get(state.id));
    assert.equal(result.approver, owner.user_id);
    assert.equal(result.decision, selected);
    assert.equal(result.action, action);
    assert.equal(result.outcome, action === 'approve' ? 'failed' : 'denied');
    assert.equal(result.source_thread, index === 1 ? thread : source);
    const payload = accepted[index - 1];
    assert.equal(payload.operation, dynamic ? 'reviewed-plan' : 'matrix-stack-status');
    assert.equal(Object.hasOwn(payload, 'command'), false);
    if (dynamic) {
      assert.equal(JSON.parse(payload.plan).script, reviewed);
      assert.equal(payload.choice, poll.answers.find(answer => answer[pollText] === label).id);
      assert.equal(payload.source_room, agents);
      assert.equal(payload.review_room, audit);
      const ids = [[agents, source], [audit, state.proposal_event], [audit, state.poll_event], [audit, selected]];
      const receipts = await Promise.all(ids.map(([room, id]) => api('/rooms/' + encodeURIComponent(room) + '/event/' + encodeURIComponent(id), undefined, root.access_token)));
      assert.deepEqual(receipts.map(item => item.type), Array(4).fill('m.room.encrypted'));
      assert.deepEqual(receipts.map(item => item.sender), [sender, root.user_id, root.user_id, owner.user_id]);
      assert.equal(receipts[3].origin_server_ts, payload.decided);
      assert.equal(receipts[3].content['m.relates_to']?.event_id, state.poll_event);
    }
    await waitFor(() => database.prepare('SELECT source_notified FROM tasks WHERE id=?').get(state.id).source_notified);
    await waitFor(() => database.prepare('SELECT closed FROM review_polls WHERE id=?').get(state.id).closed);
  }
  await waitFor(() => archived.filter(record => record.workspace === 'root-operations').length === 12);
  await waitFor(() => archived.find(record => record.workspace === 'agents-team' && record.data.evidence?.operation));
  const auditRecords = archived.filter(record => record.workspace === 'root-operations');
  for (const id of new Set(auditRecords.map(record => record.data.operation.id))) {
    const entries = auditRecords.filter(record => record.data.operation.id === id);
    assert.deepEqual(entries.map(record => record.event), ['root:requested', 'root:proposed', 'root:decided', 'root:finished']);
    assert.deepEqual(entries.map(record => record.data.execution.sequence), [0, 1, 2, 3]);
    assert.equal(new Set(entries.map(record => record.data.execution.task_id)).size, 1);
  }
  const malformed = await sendEncrypted(reader, agents, 'fixture-malformed',
    mentionMessage('Invalid review\n\nROOT_REVIEW: private-marker', {id: root.user_id, name: 'VPS Manager'}));
  const invalid = await waitFor(() => database.prepare('SELECT * FROM tasks WHERE id=? AND source_notified=1')
    .get(digest(agents + ':' + malformed).slice(0,32)));
  assert.equal(invalid.outcome, 'proposal_failed');
  assert.match(invalid.summary, /one JSON proposal/u);
  assert.doesNotMatch(invalid.summary, /private-marker/u);
  assert.equal(invalid.proposal_event, null);
  assert.equal(database.prepare('SELECT id FROM tasks WHERE event=?').get(forged), undefined);
  assert.equal(accepted.length, 3);
  console.log('VPS Manager handoffs and reviewed plans reached the encrypted poll, the owner vote and the source receipt.');
  console.log('Invalid input produced a specific reason, without publishing a poll or running a command.');
  const maintenance = await sendEncrypted(reader, agents, 'fixture-maintenance',
    mentionMessage('maintenance dashboard fixture', {id: root.user_id, name: 'VPS Manager'}));
  const approval = await waitFor(() => {
    const task = database.prepare('SELECT tasks.*, review_polls.event AS poll_event FROM tasks LEFT JOIN review_polls USING(id) WHERE tasks.event=?').get(maintenance);
    assert.equal(task?.outcome ?? null, null, task?.summary);
    return task?.poll_event && task;
  });
  assert.deepEqual(maintenanceCalls.map(value => value.maintenance), ['prepare','seal']);
  assert.equal(accepted.length, 3);
  await timeline();
  const notice = await reader.getEvent(audit, approval.proposal_event);
  const poll = await reader.getEvent(audit, approval.poll_event);
  assert.ok(notice.content.body.includes(fixtureDiff));
  assert.ok(notice.content.body.includes(release.diff_sha256));
  const vote = await castVote('fixture-maintenance-accept', approval.poll_event, poll.content[pollStart].answers[0].id);
  const finished = await waitFor(() => database.prepare('SELECT * FROM tasks WHERE id=? AND source_notified=1').get(approval.id));
  assert.equal(finished.outcome, 'completed');
  assert.equal(finished.decision, vote);
  assert.equal(accepted[3].operation, 'maintenance-release');
  assert.deepEqual(JSON.parse(accepted[3].plan), release);
  await waitFor(() => database.prepare('SELECT id FROM maintenance_notices WHERE id=?').get('fixture-recovery'));
  console.log('The tested release diff reached the encrypted poll and its exact approval. Autonomous recovery added a separate audit notice.');
} catch (error) {
  console.error(error);
  process.exitCode = 1;
} finally {
  worker.kill();
  await once(worker, 'exit');
  sockets.close();
  context.close();
  database?.close();
  await delay(100);
  process.reallyExit(process.exitCode ?? 0);
}
