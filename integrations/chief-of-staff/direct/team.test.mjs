import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { DatabaseSync } from 'node:sqlite';
import { test } from 'node:test';
import { improvementJobs, weeklyWindow } from './team.mjs';
import { checkTargetTools, publicUrl, research, useTool } from '../../../agents/connector/team-tools.mjs';
import { needFromReply } from '../../../agents/connector/capability-dispatch.mjs';
import { acceptsContact } from '../../../agents/connector/agent-catalog.mjs';
import { assignedHandoff } from '../../../agents/connector/managed-task.mjs';
import { threadReply, turnKey } from '../../../agents/connector/matrix-routing.mjs';
import { mentionMessage } from '../../../agents/connector/matrix-format.mjs';

const actor = '@editor:fixture.test', coach = '@coach:fixture.test', owner = '@operator:fixture.test';
const roster = [{id:actor}, {id:coach}];
const reply = {actor,thread:'$thread',request:'$task',reply:'$report',status:'blocked', outcome:'blocked'};

test('employment, tool gaps, credentials and outages reach the appropriate desk', () => {
  const role = {...reply,need:{kind:'role', topic:'release-notes',resource:'task:notes'}};
  assert.equal(improvementJobs('hiring-manager',[role,role],roster).size,0);
  assert.equal(improvementJobs('hiring-manager',[role,{...role,request:'$second'}],roster).size,1);
  for (const [type, count] of [['tool',1],['credentials',1],['outage',0],['role',0]]) {
    const e = {...reply,need:{kind:type,topic:'github'}};
    assert.equal(improvementJobs('scouter',[e,e],roster).size,count);
  }
  assert.equal(improvementJobs('coach',[reply,{...reply,actor:'@outside:x',request:'$fake'}],roster).size,1);
  const editor = {capabilities:[{code:'release-notes'}]};
  assert.equal(needFromReply('OFFICE_NEED: {"kind":"tool","topic":"GitHub","capability":"release-notes"}',editor)?.topic,'GitHub');
  assert.equal(needFromReply('OFFICE_NEED: {"kind":"tool","topic":"GitHub","capability":"billing"}',editor),null);
  assert.equal(weeklyWindow(Date.parse('2026-11-23T08:59:59Z')), '2026-11-16T09:00:00.000Z');
  assert.equal(weeklyWindow(Date.parse('2026-11-23T09:00:00Z')), '2026-11-23T09:00:00.000Z');
  assert.equal(weeklyWindow(Date.now(),'off'),null);
});

test('a return to the actual assigner can cross a removed contact without authorizing a new assignment', async () => {
  const names = {[actor]:'Editor',[coach]:'Coach'}, target = {id:actor,name:'Editor'};
  const task = {id:'$task', request:'$task',thread:'$task',number:1};
  const wanted = {capability:'review',resource:'task:sample'};
  const assignment = threadReply(task,'Review the draft.',[target],null,[],{},{target,requirement:wanted});
  const complete = threadReply({...task,id:'$step',number:2,delegator:coach},'Here is the evidence.',[],null,[],{},
    {returnTo:{id:coach,name:'Coach'}});
  assert.deepEqual(complete['m.mentions'].user_ids,[coach]);
  const blocked = threadReply({...task,id:'$step',number:2},'TASK_OUTCOME: ' + JSON.stringify({
    status:'blocked',blocker:'Need the source file',unblocker:'Requester',action:'Attach the file.'}),[],null,[],{},
    {returnTo:{id:coach,name:'Coach'}});
  assert.match(blocked.body,/Need the source file/);
  assert.equal(blocked['net.matrix_agents.task_outcome'],undefined);
  const envelope = {sender:actor,content:complete};
  const policy = {snapshot:{contactPolicy:true,agents:[]},fresh:true};
  assert.equal(acceptsContact(policy,envelope,coach),true);
  const previous = new Map([['$step',{sender:coach,content:assignment}],['$task',{sender:owner,content:mentionMessage('Start',{id:coach,name:'Coach'})}]]);
  assert.equal(await assignedHandoff(envelope,'$task','$task',coach,names,[owner],id=>previous.get(id)),true);
  previous.get('$step').sender=actor;
  assert.equal(await assignedHandoff(envelope,'$task','$task',coach,names,[owner],id=>previous.get(id)),false);
  complete[turnKey].handoff=wanted;
  assert.equal(acceptsContact(policy,envelope,coach),false);
});

test('publisher research handles partial outages and adapters enforce the approved scope', async () => {
  const results = await research('release tooling',async url => {
    if (url.includes('registry.model')) throw new Error('offline');
    return Response.json({items:[{full_name:'example/notes',html_url:'https://github.com/example/notes'}]});
  });
  assert.equal(results[0].name,'example/notes');
  await assert.rejects(research('https://private.invalid/path'));
  assert.throws(()=>publicUrl('https://user:password@example.org'));
  const skill = {kind:'skill',source:'https://example.org/skill.txt',sha256:createHash('sha256').update('Use the checklist.').digest('hex')};
  assert.equal(await useTool(skill,'read',{}, {fetch:async()=> new Response('Use the checklist.')}),'Use the checklist.');
  await assert.rejects(useTool(skill,'read',{}, {fetch:async()=>new Response('Edited')}),/digest/);
  const github = {kind:'github',repository:'sample/release'};
  let args;
  const deps={read:async()=> 'fixture-token', run:async(_cmd, argv, options)=> {
    args=argv; assert.equal(options.env.GH_TOKEN,'fixture-token'); return {stdout:'[]'};
  }};
  await useTool(github,'issues',{},deps);
  assert.equal(args.at(-1),'repos/sample/release/issues?per_page=20');
  await assert.rejects(useTool(github,'delete',{},deps));
});

test('MCP grants verify server version, discovery and the exact tool name', async () => {
  const manifest = {kind:'mcp',endpoint:'https://tools.example.org/mcp',version:'2.0',methods:['lookup']};
  const called = [];
  const fetch = async (_url, init) => {
    const req = JSON.parse(init.body);
    called.push(req.method);
    if (req.method.startsWith('notifications/')) return new Response(null,{status:202});
    const values={initialize:{protocolVersion:req.params?.protocolVersion,capabilities:{tools:{}},serverInfo:{name:'fixture',version:'2.0'}},
      'tools/list':{tools:[{name:'lookup',inputSchema:{type:'object'}}]}, 'tools/call':{content:[{type:'text',text:'found'}]}};
    return Response.json({jsonrpc:'2.0',id:req.id,result:values[req.method]});
  };
  assert.deepEqual(await useTool(manifest,'verify',{},{fetch}),['lookup']);
  assert.equal((await useTool(manifest,'lookup',{q:'test'},{fetch})).content[0].text,'found');
  await assert.rejects(useTool({...manifest,version:'3.0'},'verify',{},{fetch}),/version/);
  await assert.rejects(useTool(manifest,'change',{}, {fetch}),/outside/);
  assert.equal(called.filter(x=>x==='tools/call').length,1);
});

test('target verification is durable and delivery retries reuse its decision', async () => {
  const db = new DatabaseSync(':memory:');
  const change='a'.repeat(64);
  const target={toolChecks:{fixture:{change,tool:{kind:'unsupported'}}}};
  const reports=[];
  await assert.rejects(checkTargetTools(target,db,async ()=>{throw new Error('Context unavailable');}));
  await checkTargetTools(target,db,async e=>reports.push(e));
  await checkTargetTools(target,db,async e=>reports.push(e));
  assert.equal(reports.length,1);
  assert.deepEqual([reports[0].event,reports[0].data.status,reports[0].data.change],['office:tool_check','failed',change]);
  db.close();
});
