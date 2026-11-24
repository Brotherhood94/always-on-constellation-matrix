import assert from 'node:assert/strict';
import { acceptedMessage, directReplyKey, dotPlanKey, mentionMessage, returnDelegatedReview, taskOutcomeKey, threadReply } from '../connector/matrix-routing.mjs';

const userId = '@analyst:matrix.test';
const owner = '@operator:matrix.test';
const reviewer = '@reviewer:matrix.test';
const coder = '@coder:matrix.test';
const roomId = '!agents:matrix.test';
const context = { roomId, owner, userId, agents: [userId, reviewer, coder], activatedAt: 100 };
const tag = '<a href="https://matrix.to/#/@analyst:matrix.test">Analyst</a>';
const message = {
  room_id: roomId, sender: owner, type: 'm.room.message',
  event_id: '$message', origin_server_ts: 101,
  content: {
    msgtype: 'm.text', body: 'Analyst hello', formatted_body: tag + ' hello',
    'm.mentions': { user_ids: [userId] },
  },
};
const event = acceptedMessage(message, context);
assert.deepEqual(event, { id: '$message', thread: '$message', body: 'Analyst hello', request: '$message', number: 1, delegator: null });
assert.equal(acceptedMessage({ ...message, sender: reviewer }, context), null);
assert.equal(acceptedMessage({ ...message, origin_server_ts: 99 }, context), null);
for (const content of [
  { formatted_body: 'Analyst hello' },
  { formatted_body: `<mx-reply>${tag}</mx-reply>hello` },
  { formatted_body: `<blockquote>${tag}</blockquote>hello` },
  { 'm.mentions': { user_ids: [userId, reviewer] } },
  { 'm.mentions': { user_ids: [userId, coder] } },
  { 'm.mentions': undefined },
  { 'm.relates_to': { rel_type: 'm.replace', event_id: '$edited' } },
]) assert.equal(acceptedMessage({ ...message, content: { ...message.content, ...content } }, context), null);

const planner = '@dot:matrix.test';
const plan = { ...message, sender: planner, content: {
  ...message.content, [dotPlanKey]: { task_id: 'test-plan' },
} };
assert.deepEqual(acceptedMessage(plan, { ...context, planner }), event);
assert.equal(acceptedMessage(plan, context), null);
assert.equal(acceptedMessage({ ...plan, sender: '@stranger:matrix.test' }, { ...context, planner }), null);
assert.equal(acceptedMessage({ ...message, sender: planner }, { ...context, planner }), null);
const threadedPlan = { ...plan, content: { ...plan.content, 'm.relates_to': { rel_type: 'm.thread', event_id: '$other' } } };
assert.equal(acceptedMessage(threadedPlan, { ...context, planner }), null);

const thread = acceptedMessage({ ...message, event_id: '$reply', content: {
  ...message.content, 'm.relates_to': { rel_type: 'm.thread', event_id: '$message' },
} }, context);
assert.equal(thread.thread, '$message');
assert.equal(thread.request, '$reply');
assert.deepEqual(threadReply(thread, 'result')['m.relates_to']['m.in_reply_to'], { event_id: '$reply' });

const analyst = { id: userId, name: 'Analyst' };
const reviewerPeer = { id: reviewer, name: 'Reviewer' };
const coderPeer = { id: coder, name: 'Coder' };
const peers = [analyst, reviewerPeer, coderPeer];
const recipients = [{ id: planner, name: 'Dot' }, { id: owner, name: 'Operator' }];
const markdown = '**Summary**\n\n- *first*\n- `second`\n\n1. Next\n\n```sh\necho hello\n```\n\n[Guide](https://example.test/guide)';
const rich = mentionMessage(markdown);
assert.equal(rich.body, markdown);
assert.equal(rich.format, 'org.matrix.custom.html');
assert.deepEqual(rich['m.mentions'], {});
for (const token of ['<strong>Summary</strong>', '<ul>', '<em>first</em>', '<code>second</code>', '<ol>', '<pre><code', '<a href="https://example.test/guide">Guide</a>']) {
  assert.ok(rich.formatted_body.includes(token), token);
}
const tagged = mentionMessage('- **Done**', [recipients[1], recipients[1]]);
assert.deepEqual(tagged['m.mentions'], { user_ids: [owner] });
assert.equal(tagged.body, 'Operator - **Done**');
assert.match(tagged.formatted_body, /<p><a href="https:\/\/matrix\.to\/#\/@operator:matrix\.test">Operator<\/a><\/p>\n<ul>/);
const unsafe = mentionMessage('<script>alert(1)</script>\n\n[link](javascript:alert%281%29)\n\n![external](https://example.test/tracker.png)');
assert.match(unsafe.formatted_body, /&lt;script&gt;/);
assert.doesNotMatch(unsafe.formatted_body, /<script|href="javascript:|<img/);
assert.equal(mentionMessage('- x\n'.repeat(7000)).formatted_body, undefined);
const completed = { status: 'completed', result: 'Saved the report.', verification: 'Reopened report.md and checked all sections.' };
const blocked = { status: 'blocked', blocker: 'The build log is missing.', unblocker: 'Operator', action: 'Upload the build log in this thread.' };
const outcomeLine = value => `TASK_OUTCOME: ${JSON.stringify(value)}`;
const verified = threadReply(event, 'Detailed results.\n' + outcomeLine(completed), peers, undefined, recipients);
assert.deepEqual(verified['m.mentions'].user_ids, [planner, owner]);
assert.deepEqual(verified[taskOutcomeKey], completed);
assert.equal(verified['m.relates_to'].event_id, event.thread);
assert.match(verified.body, /Verified: Reopened report\.md/);
assert.match(verified.body, /Detailed results/);
assert.match(verified.formatted_body, /@dot:matrix\.test">Dot<\/a>.*@operator:matrix\.test">Operator<\/a>/);
assert.match(verified.formatted_body, /<strong>Task completed<\/strong>[\s\S]*<strong>Result:<\/strong>[\s\S]*<strong>Verified:<\/strong>/);
const shortResult = { status: 'completed', result: 'The code is `7f6c92`', verification: 'Read the attachment.' };
const concise = threadReply(event, 'The code is 7f6c92.\n' + outcomeLine(shortResult), peers, undefined, recipients);
assert.deepEqual(concise[taskOutcomeKey], shortResult);
assert.equal(concise.body.match(/7f6c92/g)?.length, 1);
assert.match(concise.body, /\nResult: The code is `7f6c92`\nVerified: Read the attachment\./);
assert.match(concise.formatted_body, /<strong>Result:<\/strong> The code is <code>7f6c92<\/code>/);
const stopped = threadReply(event, outcomeLine(blocked), peers, undefined, recipients);
assert.deepEqual(stopped[taskOutcomeKey], blocked);
assert.match(stopped.body, /Can unblock: Operator\nNext step: Upload the build log/);
assert.match(stopped.formatted_body, /<strong>Blocker:<\/strong>[\s\S]*<strong>Can unblock:<\/strong>[\s\S]*<strong>Next step:<\/strong>/);
assert.deepEqual(stopped['m.mentions'].user_ids, [planner, owner]);
for (const pending of ['Waiting for the worker.', outcomeLine({ ...blocked, action: '' }), outcomeLine({ ...completed, verification: '' }), 'TASK_OUTCOME: invalid']) {
  const result = threadReply(event, pending, peers, undefined, recipients);
  assert.deepEqual(result['m.mentions'], {});
  assert.equal(result[taskOutcomeKey], undefined);
}
const intermediate = threadReply(event, `Results.\n${outcomeLine(completed)}\nHANDOFF: Reviewer`, peers, undefined, recipients);
assert.deepEqual(intermediate['m.mentions'].user_ids, [reviewer]);
assert.equal(intermediate[taskOutcomeKey], undefined);
const directInfo = threadReply({ ...event, replyTo: owner }, outcomeLine(completed), peers, 'Operator', recipients);
assert.deepEqual(directInfo['m.mentions'].user_ids, [owner]);
assert.equal(directInfo[taskOutcomeKey], undefined);
const limitOutcome = threadReply({ ...event, number: 15 }, 'Still running.\nHANDOFF: Reviewer', peers, undefined, recipients);
assert.equal(limitOutcome[taskOutcomeKey].status, 'blocked');
assert.deepEqual(limitOutcome['m.mentions'].user_ids, [planner, owner]);
const guest = '@guest:matrix.test';
const coderTag = `<a href="https://matrix.to/#/${coder}">Coder</a>`;
const coderContext = { ...context, userId: coder, planner };
const coderMessage = (text, content = {}) => ({ ...message, event_id: '$direct', content: {
  ...message.content, body: text, formatted_body: `${coderTag} ${text}`, 'm.mentions': { user_ids: [coder] }, ...content,
} });
const directMessage = coderMessage('Qual è il link della repo? Rispondimi direttamente.', {
  'm.relates_to': { rel_type: 'm.thread', event_id: '$existing' },
});
const direct = acceptedMessage(directMessage, coderContext);
assert.equal(direct.replyTo, owner);
assert.equal(direct.thread, '$existing');
assert.equal(acceptedMessage(coderMessage('A che punto è il deploy?'), coderContext).replyTo, owner);
assert.equal(acceptedMessage(coderMessage('Qual è il link della repo?'), coderContext).replyTo, owner);
assert.equal(acceptedMessage(coderMessage('Qual è lo stato? Chiedi a Reviewer di verificare.'), coderContext).replyTo, undefined);
assert.equal(acceptedMessage(coderMessage('Create a status page and hand it off to Reviewer.'), coderContext).replyTo, undefined);
assert.equal(acceptedMessage(coderMessage('Reply directly to Reviewer.'), coderContext).replyTo, undefined);
assert.equal(acceptedMessage(coderMessage('Prepare a patch. <blockquote>Rispondimi direttamente.</blockquote>'), coderContext).replyTo, undefined);
assert.equal(acceptedMessage({ ...directMessage, sender: guest }, coderContext), null);
assert.equal(acceptedMessage({ ...directMessage, sender: reviewer }, coderContext), null);
const dotMessage = { ...coderMessage('Report the current status of the repository.', { [dotPlanKey]: { task_id: 'dot-status' } }), sender: planner, event_id: '$dot-info' };
const dotDirect = acceptedMessage(dotMessage, coderContext);
assert.equal(dotDirect.replyTo, planner);
assert.equal(dotDirect.thread, '$dot-info');
const terminal = threadReply(dotDirect, 'Ecco il link.\nHANDOFF: Reviewer', [reviewerPeer], 'Dot');
assert.equal(terminal[directReplyKey], true);
assert.equal(terminal['m.relates_to'].event_id, '$dot-info');
assert.deepEqual(terminal['m.relates_to']['m.in_reply_to'], { event_id: '$dot-info' });
assert.deepEqual(terminal['m.mentions'], { user_ids: [planner] });
assert.doesNotMatch(terminal.body, /HANDOFF/);
const ownerReply = threadReply(direct, 'Ecco il link.', peers, 'Operator');
assert.equal(ownerReply.body, 'Operator Ecco il link.');
assert.deepEqual(ownerReply['m.mentions'], { user_ids: [owner] });
assert.match(ownerReply.formatted_body, /@operator:matrix\.test">Operator<\/a>/);
assert.match(threadReply(direct, 'Done.', peers, '<Operator>').formatted_body, /&lt;Operator&gt;/);

const newAgent = '@release-bot:matrix.test';
const newPeer = '@build-bot:matrix.test';
const registeredAgents = [...context.agents, newAgent, newPeer];
const displayNames = { [newPeer]: 'Build QA+' };
for (const recipient of registeredAgents) {
  const receiver = { ...context, userId: recipient, agents: registeredAgents, planner, displayNames };
  const question = (sender, text, content = {}) => ({ ...message, sender, content: {
    ...message.content, body: text, formatted_body: `<a href="https://matrix.to/#/${recipient}">Worker</a> ${text}`,
    'm.mentions': { user_ids: [recipient] }, ...content,
  } });
  const ownerQuestion = acceptedMessage(question(owner, 'A che punto è il deploy?', {
    'm.relates_to': { rel_type: 'm.thread', event_id: '$existing' },
  }), receiver);
  const plannerQuestion = acceptedMessage(question(planner, 'What is the current status?', {
    [dotPlanKey]: { task_id: 'dot-default' },
  }), receiver);
  assert.equal(ownerQuestion.replyTo, owner);
  assert.equal(ownerQuestion.thread, '$existing');
  assert.equal(plannerQuestion.replyTo, planner);
  for (const request of [ownerQuestion, plannerQuestion]) {
    const reply = threadReply(request, 'Ready.\nHANDOFF: Reviewer', peers);
    assert.deepEqual(reply['m.mentions'], { user_ids: [request.replyTo] });
    assert.doesNotMatch(reply.body, /HANDOFF/);
  }
  if (recipient !== newPeer) {
    assert.equal(acceptedMessage(question(owner, 'What is the current status? Ask Build QA+ to verify.'), receiver).replyTo, undefined);
  }
}

let incoming = event;
for (let number = 1; number <= 15; number++) {
  const [sender, recipient] = number % 2 ? [analyst, reviewerPeer] : [reviewerPeer, analyst];
  const content = threadReply(incoming, `Result <${number}>\nHANDOFF: ${recipient.name}`, peers.filter(peer => peer.id !== sender.id));
  assert.equal(content['m.relates_to'].event_id, event.thread);
  if (number === 15) {
    assert.deepEqual(content['m.mentions'], {});
    assert.doesNotMatch(content.body, /HANDOFF/);
  } else {
    assert.deepEqual(content['m.mentions'], { user_ids: [recipient.id] });
    assert.match(content.formatted_body, /&lt;\d+&gt;/);
  }
  const handoffEvent = { ...message, sender: sender.id, event_id: `$hop-${number}`, content };
  const recipientContext = { ...context, userId: recipient.id };
  assert.equal(acceptedMessage(handoffEvent, { ...context, userId: sender.id }), null);
  assert.equal(acceptedMessage({ ...handoffEvent, sender: '@stranger:matrix.test' }, recipientContext), null);
  incoming = acceptedMessage(handoffEvent, recipientContext);
  if (number < 15) {
    assert.equal(incoming.request, event.request);
    assert.equal(incoming.number, number + 1);
    assert.equal(incoming.delegator, sender.id);
  } else {
    assert.equal(incoming, null);
  }
}
const final = threadReply(event, 'Done.', [reviewerPeer, coderPeer]);
assert.deepEqual(final['m.mentions'], {});
assert.equal(final.body, 'Done.');
const review = 'Review complete. The proposal needs a clearer threshold.';
const delegated = { ...event, number: 2 };
assert.equal(returnDelegatedReview(delegated, outcomeLine(blocked), analyst), outcomeLine(blocked));
assert.match(returnDelegatedReview(delegated, `Results.\n${outcomeLine(completed)}`, analyst), /^Results\.\n\nHANDOFF: Analyst$/);
const returned = returnDelegatedReview(delegated, review, analyst);
assert.equal(returnDelegatedReview(delegated, returned, analyst), returned);
const reviewMessage = threadReply(delegated, returned, [analyst, coderPeer]);
assert.deepEqual(reviewMessage['m.mentions'], { user_ids: [analyst.id] });
assert.equal(reviewMessage.body, `Analyst ${review}`);
assert.equal(returnDelegatedReview(event, review, analyst), review);
assert.equal(returnDelegatedReview({ ...event, number: 15 }, review, analyst), review);
const codeTask = threadReply(event, 'Implement a function.\nHANDOFF: Coder', [reviewerPeer, coderPeer]);
const forCoder = acceptedMessage({ ...message, event_id: '$code-task', sender: userId, content: codeTask }, { ...context, userId: coder });
assert.equal(forCoder.delegator, userId);
const reviewTask = threadReply(forCoder, 'Review the function.\nHANDOFF: Reviewer', [analyst, reviewerPeer]);
const forReviewer = acceptedMessage({ ...message, event_id: '$review-task', sender: coder, content: reviewTask }, { ...context, userId: reviewer });
assert.equal(forReviewer.delegator, coder);
const codeReview = threadReply(forReviewer, returnDelegatedReview(forReviewer, review, coderPeer), [analyst, coderPeer]);
assert.deepEqual(codeReview['m.mentions'], { user_ids: [coder] });
assert.equal(codeReview['m.relates_to'].event_id, event.thread);

console.log('Matrix mentions, rich text, handoffs and reaction routing passed.');
