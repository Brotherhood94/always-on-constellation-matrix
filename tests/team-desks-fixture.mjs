// Exercise the migrated private rooms with actual devices, encryption and proposal-bound votes.
import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, writeFile } from 'node:fs/promises';
import { createServer, request } from 'node:http';
import { spawn } from 'node:child_process';
import { createHmac } from 'node:crypto';
import { once } from 'node:events';
import { createServer as createSecureServer } from 'node:https';
import { createServer as createSocket } from 'node:net';
import { DatabaseSync } from 'node:sqlite';
import { setTimeout as delay } from 'node:timers/promises';
import { openRoomClient, sendEncrypted } from './matrix-session.mjs';
import { mentionMessage } from './matrix-format.mjs';
import { createRosterApproval } from './chief-of-staff/direct/roster-approval.mjs';
import { openDesk } from './chief-of-staff/direct/team.mjs';
import { WebSocketServer } from 'ws';

const url='http://synapse:8008', domain='fixture.test', owner='@operator:'+domain;
const password='synthetic-test-passphrase-88';
const source = await mkdtemp('/tmp/team-desks-');
const records = [];
const executionRecords = [];
let officeEvidence = [];
const graph = createServer(async (req,res) => {
  assert.ok(['/office/review/recent','/office/review/open','/events'].includes(req.url));
  assert.equal(req.headers.authorization,'Bearer fixture-context');
  let message = '';
  for await (const chunk of req) message += chunk;
  if (req.url === '/events') {
    executionRecords.push(JSON.parse(message));
    return res.writeHead(200,{'content-type':'application/json'}).end('{}');
  }
  const results = req.url.endsWith('/recent') ? officeEvidence : [];
  res.writeHead(200,{'content-type':'application/json'}).end(JSON.stringify({results}));
});
const host = createSocket(socket => {
  let input='';
  socket.setEncoding('utf8').on('data',chunk=>{
    input+=chunk;
    if (!input.endsWith('\n')) return;
    const receipt = JSON.parse(input);
    records.push(receipt);
    socket.end(JSON.stringify({status:JSON.parse(receipt.operation).kind==='add' ? 'provisioned' : 'retired'}));
  });
});
await Promise.all([new Promise(r=>graph.listen(8000,r)),new Promise(r=>host.listen('/run/office-roster.sock',r))]);
process.env.ROSTER_MANAGEMENT='true';
await mkdir('/state',{recursive:true});
await mkdir('/catalog',{recursive:true});
await writeFile('/catalog/integrations.json', JSON.stringify({
  time: Math.floor(Date.now() / 1000),
  services: { 'Telegram Agent': 'running', 'Telegram engine': 'healthy' },
}));
await writeFile('/context-token','fixture-context');
await writeFile('/roster-key','fixture-desk');
await mkdir('/acp');
const ca = await readFile('/desk-tls/ca.pem');
await writeFile('/acp/ca.pem', ca);
await writeFile('/acp/credential-token', 'fixture-acp');
await writeFile('/state/workshop.json', '[]');
process.env.TEAM_ROLE='hiring-manager';
const secured = createSecureServer({key:await readFile('/desk-tls/key.pem'), cert:ca});
const acp = new WebSocketServer({server:secured, path:'/api/v1/acp'});
acp.on('connection', (socket, request) => {
  assert.equal(request.headers.authorization, 'Bearer fixture-acp');
  socket.on('message', raw => {
    const {id, method, params} = JSON.parse(raw);
    const send = message => socket.send(JSON.stringify({jsonrpc:'2.0', ...message}));
    if (method === 'initialize') send({id, result:{protocolVersion:params.protocolVersion, agentCapabilities:{loadSession:true}}});
    if (method === 'session/new') send({id, result:{sessionId:'staffing-fixture'}});
    if (method !== 'session/prompt') return;
    const prompt = params.prompt[0].text;
    const officeRequest = prompt.includes('For an explicit request to hire') && prompt.split('Request:\n').at(-1);
    const key = officeRequest && /\bresearch-fixture(?:-dot)?\b/u.exec(officeRequest)?.[0];
    const input = key ? null : JSON.parse(prompt.split('\n').at(-1));
    let answer;
    if (key) {
      answer = 'I will ask Hiring Manager to prepare the private proposal.\nHANDOFF: ' +
        JSON.stringify({ agent: 'hiring-manager', capability: 'staffing', resource: 'task:hiring',
          staffing: { action: 'hire', key, brief: 'Compare and cite public sources.', web: true } });
    } else if (prompt.startsWith('Group the following')) {
      assert.deepEqual(input.map(item => item.capability), ['release-notes', 'customer-change-summary']);
      answer = {clusters:[{duty:'Write customer-facing release notes', ids:[1,2]}]};
    } else if (prompt.startsWith('Identify the uncovered duty')) {
      assert.equal(input.job.mode, 'semantic-gap');
      assert.equal(input.job.evidence.length, 2);
      const telegram = input.roster.find(agent => agent.key === 'telegram-agent');
      assert.equal(telegram?.status, 'Available');
      assert.ok(telegram.capabilities.some(skill => skill.code === 'telegram-chat-link'));
      answer = {message:'Both releases need a writer for the customer summary.', key:'release-editor',
        brief:'Write customer-facing release notes.', first_task:'Draft the next release summary.'};
    } else {
      assert.match(input.key, /^(release-editor|research-fixture(?:-dot)?)$/u);
      answer = {name:input.key === 'release-editor' ? 'Release Editor' : input.key, model:'fixture-model', effort:'low', limits:'Use verified sources.',
        capabilities:[{code:'research', description:'Compare sources and cite findings.', resources:['task:*']}]};
    }
    send({method:'session/update', params:{sessionId:params.sessionId,
      update:{sessionUpdate:'agent_message_chunk', content:{type:'text', text:typeof answer === 'string' ? answer : JSON.stringify(answer)}}}});
    send({id, result:{stopReason:'end_turn'}});
  });
});
await new Promise(resolve => secured.listen(0, resolve));
process.env.INSTANCE_ACP_URL='wss://localhost:'+secured.address().port+'/api/v1/acp';
const alias = async name => {
  const reply=await fetch(url+'/_matrix/client/v3/directory/room/'+encodeURIComponent('#'+name+':'+domain));
  assert.ok(reply.ok);
  return (await reply.json()).room_id;
};
async function participant(key,room,device) {
  const response=await fetch(url+'/_matrix/client/v3/login',{method:'POST',headers:{'content-type':'application/json'},
    body:JSON.stringify({type:'m.login.password',user:key,password,device_id:device})});
  assert.ok(response.ok);
  const state=source+'/'+device;
  await mkdir(state);
  await writeFile(state+'/matrix-token',(await response.json()).access_token);
  const client=await openRoomClient({url,roomId:room,state,userId:'@'+key+':'+domain,deviceId:device,name:key});
  let cursor;
  client.syncingTimeout=0;
  return {client,state,async sync(){
    const update=await client.doSync(cursor);
    cursor=update.next_batch;
    await client.processSync(update);
  }};
}
async function eventually(check, attempts = 45) {
  for(let attempt=0;attempt<attempts;attempt++){
    const value=await check();
    if(value) return value;
    await delay(200);
  }
  assert.fail('Expected an encrypted desk result.');
}

const children = new Set();
function launch(file, env) {
  const child = spawn(process.execPath, [file], { env: { ...process.env, ...env }, stdio: ['ignore', 'pipe', 'inherit'] });
  children.add(child);
  child.stdout.resume();
  return child;
}

async function officeHiring(office) {
  const manager = { key: 'manager', id: '@manager:' + domain, name: 'Task Manager', role: 'coordinator',
    active: true, status: 'Available', limits: '', capabilities: [], contacts: ['hiring-manager'] };
  const hire = { key: 'hiring-manager', id: '@hiring-manager:' + domain, name: 'Hiring Manager', role: 'worker',
    active: true, status: 'Available', limits: '',
    capabilities: [{ code: 'staffing', description: 'Staffing proposals', resources: ['task:*'] }], contacts: ['manager'] };
  const control = await alias('hiring-manager-dm');
  const reader = await participant('operator', office, 'OFFICEHIRINGACCEPTANCE');
  const reviewer = await participant('operator', control, 'HIRINGREVIEWACCEPTANCE');
  const managerAccount = await participant('manager', office, 'MATRIXMANAGER');
  const hiringAccount = await participant('hiring-manager', office, 'MATRIXHIRINGMANAGER');
  const dotAccount = await participant('dot', office, 'MATRIXDOT');
  for (const account of [managerAccount, hiringAccount, dotAccount]) {
    account.client.crypto.engine.machine.close();
    await writeFile(account.state + '/room-id', office);
  }
  await writeFile('/state/control-room-id', control);
  const snapshot = '/catalog/hiring-fixture.json';
  const publishRoster = (...specialists) => writeFile(snapshot, JSON.stringify({ version: 1, time: Math.floor(Date.now() / 1000),
    contactPolicy: true, dynamicHandoffs: true, agents: [manager, hire, ...specialists] }));
  await publishRoster();
  const base = { MATRIX_URL: url, MATRIX_SERVER_NAME: domain, MATRIX_OWNER: owner, MATRIX_PLANNER: '@dot:' + domain,
    MATRIX_CATALOG_PATH: snapshot, AGENT_WORKSPACE: '/tmp', CONTEXT_INTELLIGENCE_URL: 'http://localhost:8000' };
  const boot = (agent, account) => launch('/app/connector.mjs', { ...base, AGENT_USERNAME: agent.key,
    AGENT_NAME: agent.name, AGENT_STATE: account.state, TEAM_ROLE: agent.key === 'hiring-manager' ? agent.key : '' });
  boot(manager, managerAccount);
  let hiringProcess = boot(hire, hiringAccount);
  const secret = 'synthetic-private-staffing-token-fixture';
  await writeFile(dotAccount.state + '/bridge-token', secret);
  launch('/app/dot-bridge.mjs', { ...base, DOT_STATE: dotAccount.state });
  const events = [];
  reader.client.on('room.message', (room, event) => { if (room === office) events.push(event); });
  const hiringState = hiringAccount.state + '/connector.sqlite3';
  const managerState = managerAccount.state + '/connector.sqlite3';
  const row = (file, sql, ...values) => {
    let db;
    try { db = new DatabaseSync(file, { readOnly: true }); return db.prepare(sql).get(...values); }
    catch (error) { if (!/unable to open database|no such table/u.test(error.message)) throw error; }
    finally { db?.close(); }
  };
  let rpcId = 0;
  const tool = (name, args) => new Promise((resolve, reject) => {
    const req = request({ socketPath: dotAccount.state + '/bridge.sock', method: 'POST', path: '/mcp',
      headers: { 'x-dot-bridge-token': secret, 'content-type': 'application/json', accept: 'application/json, text/event-stream' },
    }, res => {
      let body = '';
      res.setEncoding('utf8');
      res.on('data', chunk => { body += chunk; });
      res.on('end', () => resolve(body && JSON.parse(body).result));
    });
    req.on('error', reject);
    req.end(JSON.stringify({ jsonrpc: '2.0', id: ++rpcId, method: 'tools/call', params: { name, arguments: args } }));
  });
  try {
    await reader.sync();
    await reviewer.sync();
    await eventually(async () => (await tool('list_agents', {}).catch(() => null))?.structuredContent?.agents?.length, 120);
    await eventually(() => row(managerState, "SELECT value FROM settings WHERE name='activatedAt'") &&
      row(hiringState, "SELECT name FROM sqlite_master WHERE name='office_hires'"), 120);
    const direct = await sendEncrypted(reader.client, office, 'hire-fixture-office',
      mentionMessage('Chiedi a Hiring Manager di assumere research-fixture per ricerche web.', manager));
    const dotTask = { task_id: 'hire-fixture-dot-task', first_agent: 'manager', title: 'Hire research-fixture-dot',
      plan: 'Ask Hiring Manager to hire research-fixture-dot for public web research.' };
    const dot = await tool('publish_plan', dotTask);
    assert.ok(dot.structuredContent?.thread_url, JSON.stringify(dot));
    const dotEvent = decodeURIComponent(dot.structuredContent.thread_url.split('/').at(-1));
    const settled = new Map();
    for (const [key, root, accept] of [['research-fixture', direct, true], ['research-fixture-dot', dotEvent, false]]) {
      const record = await eventually(async () => {
        await reader.sync();
        const status = events.find(event => event.content?.['org.matrixconstellation.staffing.receipt']?.request === root &&
          event.content['org.matrixconstellation.staffing.receipt'].status === 'awaiting_approval');
        const approval = row(hiringState, 'SELECT a.* FROM office_hires h JOIN roster_requests a ON h.private_source=a.request WHERE h.request=?', root);
        return status && approval?.poll ? { status, approval } : null;
      }, 220);
      assert.equal(record.status.sender, hire.id);
      assert.equal((await reader.client.getRawEvent(office, record.status.event_id)).type, 'm.room.encrypted');
      assert.equal(record.status.content['m.relates_to'].event_id, root);
      assert.doesNotMatch(record.status.content.body, /Compare and cite public sources|matrix.to/u);
      assert.ok(events.some(event => event.sender === manager.id &&
        event.content?.['net.matrix_agents.turn']?.staffing?.key === key));
      assert.equal(records.filter(value => JSON.parse(value.operation).agent === key).length, 0);
      if (accept) {
        const stopped = once(hiringProcess, 'exit');
        hiringProcess.kill();
        await stopped;
        hiringProcess = boot(hire, hiringAccount);
      }
      const poll = await eventually(async () => {
        await reviewer.sync();
        const event = await reviewer.client.getEvent(control, record.approval.poll);
        return event.type === 'org.matrix.msc3381.poll.start' ? event : null;
      });
      assert.equal((await reviewer.client.getRawEvent(control, record.approval.poll)).type, 'm.room.encrypted');
      const response = 'org.matrix.msc3381.poll.response';
      const vote = await reviewer.client.crypto.encryptRoomEvent(control, response, {
        [response]: { answers: [poll.content[poll.type].answers[accept ? 0 : 1].id] },
        'm.relates_to': { rel_type: 'm.reference', event_id: record.approval.poll },
      });
      await reviewer.client.doRequest('PUT', '/_matrix/client/v3/rooms/' + encodeURIComponent(control) +
        '/send/m.room.encrypted/vote-' + key, null, vote);
      const final = await eventually(async () => {
        await reader.sync();
        return events.find(event => event.content?.['org.matrixconstellation.staffing.receipt']?.request === root &&
          event.content['org.matrixconstellation.staffing.receipt'].status === (accept ? 'provider_required' : 'declined'));
      }, 240);
      assert.deepEqual(final.content['m.mentions'].user_ids, [accept ? owner : '@dot:' + domain]);
      assert.equal(records.filter(value => JSON.parse(value.operation).agent === key).length, Number(accept));
      const stored = row(hiringState, 'SELECT a.poll FROM office_hires h JOIN roster_requests a ON h.private_source=a.request WHERE h.request=?', root);
      assert.equal(stored.poll, record.approval.poll);
      settled.set(root, key);
    }
    const result = await eventually(async () => {
      const result = await tool('get_task', { task_id: dotTask.task_id });
      return result.structuredContent?.outcome?.status === 'blocked' ? result.structuredContent : null;
    }, 180);
    assert.equal(result.staffing.status, 'declined');
    assert.match(result.outcome.blocker, /declined the hire/u);
    assert.equal(settled.size, 2);
    assert.equal(row(hiringState, "SELECT count(*) AS n FROM roster_requests WHERE json_extract(operation,'$.agent') LIKE 'research-fixture%'").n, 2);

    const key = 'research-fixture';
    const register = url + '/_synapse/admin/v1/register';
    const { nonce } = await (await fetch(register)).json();
    const mac = createHmac('sha1', 'synthetic-test-registration-secret')
      .update([nonce, key, password, 'notadmin'].join('\0')).digest('hex');
    const registration = await fetch(register, { method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ nonce, username: key, password, admin: false, mac }) });
    assert.ok(registration.ok);
    const worker = await registration.json();
    await reader.client.doRequest('POST', '/_matrix/client/v3/rooms/' + encodeURIComponent(office) + '/invite', null, { user_id: worker.user_id });
    const joined = await fetch(url + '/_matrix/client/v3/join/' + encodeURIComponent(office), {
      method: 'POST', headers: { 'content-type': 'application/json', authorization: 'Bearer ' + worker.access_token }, body: '{}',
    });
    assert.ok(joined.ok);
    const specialist = { key, id: worker.user_id, name: 'Research fixture', role: 'worker',
      active: true, status: 'Available', limits: '', capabilities: [], contacts: ['manager'] };
    await publishRoster(specialist);
    await eventually(async () => {
      await reader.sync();
      return events.some(event => event.content?.['org.matrixconstellation.staffing.receipt']?.request === direct &&
        event.content['org.matrixconstellation.staffing.receipt'].status === 'awaiting_web');
    }, 120);
    assert.ok(!events.some(event => event.content?.['net.matrix_agents.task_outcome']?.status === 'completed' &&
      event.content['m.relates_to']?.event_id === direct));
    await publishRoster({ ...specialist, grants: { web: { kind: 'mcp', purpose: 'public-web' } } });
    await eventually(async () => {
      await reader.sync();
      return events.find(event => event.sender === manager.id && event.content?.['net.matrix_agents.task_outcome']?.status === 'completed' &&
        event.content['m.relates_to']?.event_id === direct);
    }, 120);
    const recorded = await eventually(() => executionRecords.find(item => item.data.evidence?.request === direct &&
      item.data.evidence.outcome === 'completed'), 90);
    assert.ok(recorded.data.evidence.references?.length);
    assert.doesNotMatch(JSON.stringify(recorded), /Compare and cite public sources/);
    console.log('Office and Dot preserved one encrypted hiring poll each, returned decline and provisioning in the original threads, and closed the accepted task after the simulated web tool check.');
  } finally {
    reader.client.crypto.engine.machine.close();
    reviewer.client.crypto.engine.machine.close();
  }
}

try {
  const office=await alias('office');
  for(const [actor,kind] of [['hiring-manager','retire'],['coach','coach'],['scouter','tool']]){
    const room=await alias(actor+'-dm');
    const desk=await participant(actor,room,'DESK'+actor.toUpperCase().replaceAll('-',''));
    const operator=await participant('operator',room,'REVIEW'+actor.toUpperCase().replaceAll('-',''));
    const db=new DatabaseSync(':memory:');
    try {
      await operator.sync(); await desk.sync();
      const member='@'+actor+':'+domain;
      assert.deepEqual(new Set(await desk.client.getJoinedRoomMembers(room)),new Set([owner,member]));
      let turn;
      let approval;
      let assignments;
      const operation = kind==='tool' ? {kind,agent:'coder',tool:{id:'repo-read',repository:'fixture/docs'}} :
        {kind,agent:'coder',style:'Report one verified result.',contacts:['vps-manager']};
      if(kind==='retire'){
        await writeFile('/state/control-room-id',room);
        const agent={key:'coder',name:'Coder',id:'@coder:'+domain,role:'worker',active:true,placement:'local',contacts:[],persona:{duties:['Write code.']}};
        const catalog={fresh:()=>true,current:()=>({agents:[agent]})};
        const publisher = await participant('operator',office,'OFFICEHIRING');
        try {
          await publisher.sync();
          const duties = [
            ['release-notes','task:alpha-release','Write a customer-facing summary for the alpha release.'],
            ['customer-change-summary','task:beta-release','Explain the beta changes to customers in a release announcement.'],
          ];
          for (const [topic,resource,reason] of duties) {
            const request = await sendEncrypted(publisher.client,office,'test-'+topic,mentionMessage(reason));
            const reply = await sendEncrypted(publisher.client,office,'evidence-'+topic,mentionMessage('Synthetic staffing evidence.'));
            officeEvidence.push({status:'blocked',data:JSON.stringify({evidence:{actor:agent.id,request,thread:request,reply,
              need:{kind:'role',topic,resource,reason}}})});
          }
          assignments = officeEvidence.map(record => JSON.parse(record.data).evidence.request);
          for (const n of [1, 2]) {
            const request = await sendEncrypted(publisher.client, office, 'link-chat-' + n,
              mentionMessage('Synthetic Telegram chat-link request.'));
            const reply = await sendEncrypted(publisher.client, office, 'link-chat-blocked-' + n,
              mentionMessage('Synthetic Telegram routing blocker.'));
            officeEvidence.push({ status:'blocked', data:JSON.stringify({evidence:{
              actor:agent.id, request, thread:request, reply,
              need:{kind:'role', topic:'telegram-chat-link', resource:'workspace:telegram', reason:'Link a Telegram chat.'},
            }}) });
          }
        } finally {publisher.client.crypto.engine.machine.close();}
        const service=await openDesk({db,client:desk.client,catalog,office,owner,actor});
        turn=await sendEncrypted(operator.client,room,'retire-coder',mentionMessage('retire coder'));
        await eventually(async()=>{await desk.sync(); await service.tick(); return db.prepare('SELECT poll FROM roster_requests WHERE request=?').get(turn)?.poll;});
        approval={run:async()=>{await desk.sync(); await service.tick();}};
      } else {
        const revision = 'a'.repeat(64);
        const observed = [];
        const service=createRosterApproval({db,client:desk.client,room,owner,actor,checkDestination:async()=>{},
          currentAgents:()=>[],onApplied:(op,id)=>observed.push([op,id]),
          apply:async payload=>{records.push(payload);return {status:kind==='tool'?'enabled':'applied',change:revision};}});
        turn=await sendEncrypted(desk.client,room,'proposal-origin',mentionMessage('Review this exact change for Coder.'));
        service.enqueue(turn,operation,'Cited Office evidence','agent');
        await service.tick();
        approval={run:async(id)=>{
          await desk.sync();
          await service.accept(await desk.client.getEvent(room,id),await desk.client.getRawEvent(room,id));
          await service.tick();
          if (records.some(r=>r.request===turn) && kind==='coach') assert.deepEqual(observed,[[operation,revision]]);
        }};
      }
      const row=db.prepare('SELECT proposal,poll FROM roster_requests WHERE request=?').get(turn);
      const poll=await eventually(async()=>{await operator.sync();try{return await operator.client.getEvent(room,row.poll);}catch{return null;}});
      const raw=await operator.client.getRawEvent(room,row.poll);
      assert.equal(raw.sender,member);
      assert.equal(raw.type,'m.room.encrypted');
      const start='org.matrix.msc3381.poll.start', vote='org.matrix.msc3381.poll.response';
      const choice=poll.content[start].answers[0].id;
      const envelope=await operator.client.crypto.encryptRoomEvent(room,vote,{[vote]:{answers:[choice]},'m.relates_to':{rel_type:'m.reference',event_id:row.poll}});
      const receipt=await operator.client.doRequest('PUT','/_matrix/client/v3/rooms/'+encodeURIComponent(room)+'/send/m.room.encrypted/approve',null,envelope);
      await eventually(async()=>{await approval.run(receipt.event_id);return records.find(p=>p.request===turn);});
      const record=records.find(p=>p.request===turn);
      assert.equal(JSON.parse(record.operation).kind,kind);
      assert.equal(record.actor,actor);
      assert.equal(record.poll,row.poll);
      assert.equal(record.vote,receipt.event_id);
      console.log(actor+' received its exact encrypted operator approval in its own DM.');
      if (assignments) {
        const hire = await eventually(async () => {
          await approval.run();
          return db.prepare("SELECT request, proposal, poll FROM roster_requests WHERE request<>? AND poll IS NOT NULL").get(turn);
        });
        assert.ok(!records.some(entry => entry.request === hire.request));
        const proposal = await eventually(async () => {
          await operator.sync();
          const event = await operator.client.getEvent(room,hire.proposal);
          return event.type === 'm.room.message' ? event : null;
        });
        assert.match(proposal.content.body, /Recurring duty across 2 Office assignments/u);
        for (const id of assignments) assert.ok(proposal.content.body.includes(encodeURIComponent(id)));
        assert.equal((await operator.client.getRawEvent(room,hire.poll)).type,'m.room.encrypted');
        const hirePoll = await operator.client.getEvent(room,hire.poll);
        const selection=await operator.client.crypto.encryptRoomEvent(room,vote,
          {[vote]:{answers:[hirePoll.content[start].answers[0].id]},'m.relates_to':{rel_type:'m.reference',event_id:hire.poll}});
        const response=await operator.client.doRequest('PUT','/_matrix/client/v3/rooms/'+encodeURIComponent(room)+'/send/m.room.encrypted/approve-hire',null,selection);
        const approved = await eventually(async () => {
          await approval.run(response.event_id);
          return records.find(entry => entry.request === hire.request);
        });
        assert.equal(JSON.parse(approved.operation).agent, 'release-editor');
        assert.equal(approved.vote,response.event_id);
        assert.equal(db.prepare("SELECT COUNT(*) AS count FROM roster_requests WHERE request<>?").get(turn).count,1);
        officeEvidence = [];
        console.log('Hiring Manager clustered two differently labeled Office gaps and submitted one hire only after the operator vote.');
      }
    } finally {desk.client.crypto.engine.machine.close();operator.client.crypto.engine.machine.close();db.close();}
  }
  await officeHiring(office);
} finally {
  for (const child of children) if (child.exitCode === null && child.signalCode === null) child.kill();
  for (const socket of acp.clients) socket.terminate();
  acp.close();
  secured.close();
  host.close();
  graph.close();
}
