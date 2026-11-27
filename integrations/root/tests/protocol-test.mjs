import assert from 'node:assert/strict';
import * as protocol from '../protocol.mjs';

const candidate = {
  operation: 'reviewed-script',
  summary: 'Inspect the fixture',
  impact: 'Read a synthetic source.',
  verification: 'Exit zero if available.',
  script: 'printf "%s\\n" "<fixture>"\nexit 0\n  \n',
};
const json = JSON.stringify(candidate);
const fence = '```';
const wrapped = fence + 'json\n' + json + '\n' + fence;
assert.equal(protocol.proposal(json, {}).command, candidate.script);
for (const response of [wrapped, 'My proposal follows.\n' + wrapped, 'My proposal follows.\n' + json]) {
  assert.equal(protocol.proposal(response, {}).command, candidate.script);
}
assert.throws(() => protocol.proposal(json + '\n' + json, {}), /one JSON proposal/);
assert.throws(() => protocol.proposal(wrapped + '\n' + wrapped, {}), /one JSON proposal/);
assert.throws(() => protocol.proposal(JSON.stringify({...candidate, verification: ''}), {}), /verification/);
assert.throws(() => protocol.proposal(JSON.stringify({...candidate, script: '# é\n'}), {}), /ASCII/);
const root = '@root:fixture.test';
const submitted = 'ROOT_REVIEW: ' + json;
const event = {type: 'm.room.message', content: {msgtype: 'm.text',
  body: 'Root Fixture title\n\n' + submitted, 'm.mentions': {user_ids: [root]}}};
const parsed = protocol.submittedProposal(protocol.requestText(event, root));
assert.equal(parsed.command, candidate.script);
assert.equal(parsed.hash, protocol.digest(candidate.script));
assert.equal(protocol.submittedProposal('Ordinary request'), null);
assert.throws(() => protocol.submittedProposal('ROOT_REVIEW: ' + JSON.stringify({operation: null})), /reviewed-script/);
assert.throws(() => protocol.submittedProposal('ROOT_REVIEW: malformed private input'), /one JSON proposal/);
assert.equal(protocol.submittedProposal('Quoted transcript\n> ' + submitted), null);
const manager = '@manager:fixture.test';
const config = { root, owner: '@operator:fixture.test' };
const managed = { request: '$assignment', number: 1, managed: { version: 2, target: root, manager } };
const handoff = protocol.sourceTurn({
  'm.relates_to': { rel_type: 'm.thread', event_id: managed.request },
  [protocol.agentTurnKey]: managed,
}, config, { sender: manager, event_id: '$delegation' });
for (const [action, outcome] of [['approve', 'completed'], ['approve', 'failed'], ['reject', 'denied']]) {
  const status = protocol.resultHandoff(config, { sender: manager, action, outcome }, handoff);
  assert.equal(status.recipient, manager);
  assert.equal(status.turn.request, '$assignment');
  assert.equal(status.turn.managed.dispatch, '$delegation');
  assert.equal(status.turn.managed.target, manager);
}
for (const [request, target] of [['Diagnostica Telegram', 'telegram'], ['Diagnostica synapse', 'synapse'],
  ['Diagnostica servizi', 'all'], ['Diagnostica il servizio Context Intelligence.', 'context-intelligence'],
  ['Diagnose workspace-monitor.service', 'workspace-monitor.service']]) {
  const inspection = protocol.serviceDiagnosticProposal(request);
  assert.deepEqual(JSON.parse(inspection.command), {kind:'inspect-services', target});
  assert.equal(inspection.hash, protocol.digest(inspection.command));
  assert.ok(protocol.reviewBody(inspection).includes(target));
}
assert.equal(protocol.serviceDiagnosticProposal('Diagnostica il blocco e proponi una correzione'), null);
assert.throws(() => protocol.proposal(JSON.stringify({operation:'reviewed-plan',
  plan:{kind:'inspect-services', target:'--all'}}), {}), /one service ID/);
assert.throws(() => protocol.proposal(JSON.stringify({operation:'reviewed-plan',
  plan:{kind:'inspect-services', target:'/etc/passwd'}}), {}), /one service ID/);
console.log('Planner formatting, exact script bytes, safe format errors and Manager receipts passed.');

const selection = protocol.twinRequest('twin create ' + 'a'.repeat(24) + ' fixture-model low helper,coach:fixture-other:high');
assert.deepEqual(selection.overrides, { coach: { model: 'fixture-other', effort: 'high' } });
assert.equal(selection.minutes, 120);
const twin = protocol.twinProposal(selection, 'b'.repeat(64));
assert.equal(protocol.digest(twin.command), twin.hash);
assert.match(protocol.reviewBody(twin), /coach · fixture-other · high/u);
assert.deepEqual(protocol.twinRequest('start a digital twin'), { action: 'choices' });
assert.throws(() => protocol.twinRequest('twin create ' + 'a'.repeat(24) + ' fixture-model low helper 500'));
