// Exercise the migrated private rooms with actual devices, encryption and proposal-bound votes.
import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
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
let officeEvidence = [];
const graph = createServer(async (req,res) => {
  assert.ok(['/office/review/recent','/office/review/open'].includes(req.url));
  assert.equal(req.headers.authorization,'Bearer fixture-context');
  for await (const _ of req) {}
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
    const input = JSON.parse(prompt.split('\n').at(-1));
    let answer;
    if (prompt.startsWith('Group the following')) {
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
      assert.equal(input.key, 'release-editor');
      answer = {name:'Release Editor', model:'fixture-model', effort:'low', limits:'Use verified changes.',
        capabilities:[{code:'release-notes', description:'Write release notes.', resources:['task:*']}]};
    }
    send({method:'session/update', params:{sessionId:params.sessionId,
      update:{sessionUpdate:'agent_message_chunk', content:{type:'text', text:JSON.stringify(answer)}}}});
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
  return {client,async sync(){
    const update=await client.doSync(cursor);
    cursor=update.next_batch;
    await client.processSync(update);
  }};
}
async function eventually(check) {
  for(let attempt=0;attempt<45;attempt++){
    const value=await check();
    if(value) return value;
    await delay(200);
  }
  assert.fail('Expected an encrypted desk result.');
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
} finally {
  for (const socket of acp.clients) socket.terminate();
  acp.close();
  secured.close();
  host.close();
  graph.close();
}
