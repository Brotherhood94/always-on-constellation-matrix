// Exercise the migrated private rooms with actual devices, encryption and proposal-bound votes.
import assert from 'node:assert/strict';
import { mkdir, mkdtemp, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { createServer as createSocket } from 'node:net';
import { DatabaseSync } from 'node:sqlite';
import { setTimeout as delay } from 'node:timers/promises';
import { openRoomClient, sendEncrypted } from './matrix-session.mjs';
import { mentionMessage } from './matrix-format.mjs';
import { createRosterApproval } from './chief-of-staff/direct/roster-approval.mjs';
import { openDesk } from './chief-of-staff/direct/team.mjs';

const url='http://synapse:8008', domain='fixture.test', owner='@operator:'+domain;
const password='synthetic-test-passphrase-88';
const source = await mkdtemp('/tmp/team-desks-');
const records = [];
const graph = createServer(async (req,res) => {
  assert.ok(['/office/review/recent','/office/review/open'].includes(req.url));
  assert.equal(req.headers.authorization,'Bearer fixture-context');
  for await (const _ of req) {}
  res.writeHead(200,{'content-type':'application/json'}).end(JSON.stringify({results:[]}));
});
const host = createSocket(socket => {
  let input='';
  socket.setEncoding('utf8').on('data',chunk=>{
    input+=chunk;
    if (!input.endsWith('\n')) return;
    records.push(JSON.parse(input));
    socket.end(JSON.stringify({status:'retired'}));
  });
});
await Promise.all([new Promise(r=>graph.listen(8000,r)),new Promise(r=>host.listen('/run/office-roster.sock',r))]);
process.env.ROSTER_MANAGEMENT='true';
await mkdir('/state',{recursive:true});
await writeFile('/context-token','fixture-context');
await writeFile('/roster-key','fixture-desk');
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
      const operation = kind==='tool' ? {kind,agent:'coder',tool:{id:'repo-read',repository:'fixture/docs'}} :
        {kind,agent:'coder',style:'Report one verified result.',contacts:['vps-manager']};
      if(kind==='retire'){
        await writeFile('/state/control-room-id',room);
        const agent={key:'coder',name:'Coder',id:'@coder:'+domain,role:'worker',active:true,placement:'local',contacts:[],persona:{duties:['Write code.']}};
        const catalog={fresh:()=>true,current:()=>({agents:[agent]})};
        const service=await openDesk({db,client:desk.client,catalog,office,owner,actor});
        turn=await sendEncrypted(operator.client,room,'retire-coder',mentionMessage('retire coder'));
        await eventually(async()=>{await desk.sync(); await service.tick(); return db.prepare('SELECT poll FROM roster_requests WHERE request=?').get(turn)?.poll;});
        approval={run:async()=>{await desk.sync(); await service.tick();}};
      } else {
        const service=createRosterApproval({db,client:desk.client,room,owner,actor,checkDestination:async()=>{},
          currentAgents:()=>[],apply:async payload=>{records.push(payload);return {status:kind==='tool'?'enabled':'applied'};}});
        turn=await sendEncrypted(desk.client,room,'proposal-origin',mentionMessage('Review this exact change for Coder.'));
        service.enqueue(turn,operation,'Cited Office evidence','agent');
        await service.tick();
        approval={run:async(id)=>{await desk.sync(); await service.accept(await desk.client.getEvent(room,id),await desk.client.getRawEvent(room,id)); await service.tick();}};
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
    } finally {desk.client.crypto.engine.machine.close();operator.client.crypto.engine.machine.close();db.close();}
  }
} finally {host.close();graph.close();}
