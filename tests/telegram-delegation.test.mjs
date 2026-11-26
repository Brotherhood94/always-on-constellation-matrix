import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createTelegramDelegation, draftReceipt } from '../integrations/telegram/delegation.mjs';
import { TelegramStore } from '../integrations/telegram/policy.mjs';
import { telegramIntent, telegramMention } from '../agents/connector/telegram-intent.mjs';
import { acceptedMessage, dotPlanKey, telegramOfficeAssignment } from '../agents/connector/matrix-routing.mjs';
import { mentionMessage } from '../agents/connector/matrix-format.mjs';
import { telegramReceipt } from '../agents/connector/telegram-office.mjs';

const domain = 'sample.test';
const owner = `@operator:${domain}`;
const agent = `@telegram-agent:${domain}`;
const officeRoom = `!office:${domain}`;
const controlRoom = `!telegram:${domain}`;
const caller = { actor: `@task-manager:${domain}`, sender: owner, room: officeRoom };
const direct = (id, text, sender = owner) => ({ event_id: id, type: 'm.room.message', sender,
  content: { msgtype: 'm.text', body: '@Telegram Agent, ' + text, 'm.mentions': { user_ids: [agent] } } });

test('the Office assignment and Telegram intent both require a real mention of Task Manager', () => {
  const name = 'Task Manager';
  const context = { roomId: officeRoom, owner, userId: caller.actor, agents: [caller.actor],
    activatedAt: 0, displayNames: { [caller.actor]: name } };
  const event = { room_id: officeRoom, event_id: '$assignment', sender: owner, type: 'm.room.message', origin_server_ts: 1,
    content: { msgtype: 'm.text', body: '@Task Manager, add @sample_contact to my Telegram rooms.',
      format: 'org.matrix.custom.html', formatted_body: `<a href="https://matrix.to/#/${caller.actor}">${name}</a>, add @sample_contact to my Telegram rooms.`,
      'm.mentions': { user_ids: [caller.actor] } } };
  assert.equal(acceptedMessage(event, context)?.id, event.event_id);
  const input = telegramMention(event.content, caller.actor, name);
  assert.deepEqual(telegramIntent(input, { office: true }), { action: 'open', target: '@sample_contact', type: 'direct' });
  delete event.content['m.mentions'];
  assert.equal(acceptedMessage(event, context), null);
  assert.equal(telegramMention(event.content, caller.actor, name), null);
});

test('the pinned requests and Italian Telegram requests have bounded intents', () => {
  for (const [message, action, target] of [
    ['add Sam Example to my Telegram rooms.', 'open', 'Sam Example'],
    ['open the private chat with @sample_contact.', 'open', '@sample_contact'],
    ['add the Telegram user with ID 1001 to my rooms.', 'open', 'user:1001'],
    ['add the Project Room group to my Telegram rooms.', 'open', 'Project Room'],
    ['prepare a private message to Sam Example saying tomorrow works for me.', 'draft', 'Sam Example'],
    ['prepare a message for Project Room saying the meeting starts at 10.', 'draft', 'Project Room'],
    ['summarize what I missed today in Project Room.', 'read', 'Project Room'],
    ['Find Sam on Telegram and prepare a private message saying tomorrow looks good.', 'draft', 'Sam'],
    ['Aggiungi il gruppo Progetto alle stanze di Telegram', 'open', 'Progetto'],
    ['Aggiungi il seguente utente su Telegram che ha id-peer 1001', 'open', 'user:1001'],
    ['Scrivi a Sam su Telegram che domani sarà una bella giornata', 'send', 'Sam'],
    ['Prepara una bozza per Sam dicendo ciao', 'draft', 'Sam'],
    ['Rimuovi il gruppo Project Room dallo spazio Telegram', 'unlink', 'Project Room'],
  ]) {
    assert.deepEqual([telegramIntent(message)?.action, telegramIntent(message)?.target], [action, target], message);
  }
  for (const message of ['Do not add Sam to Telegram', 'Tell me how to add Sam to Telegram', 'Add Sam and Bob to Telegram',
    'Add Sam to all Telegram rooms', '> Add Sam to Telegram', 'Add Sam to Telegram; send a message']) {
    assert.equal(telegramIntent(message, { office: true }), null, message);
  }
  assert.equal(telegramIntent('Send a message to Sam: test', { office: true }), null);
  assert.equal(telegramIntent('Send a message to Sam on Telegram: "exact text!"').text, 'exact text!');
  assert.deepEqual(telegramIntent('manda messaggio a Sam "ciao bello"'),
    { action: 'send', target: 'Sam', text: 'ciao bello' });
  assert.deepEqual(telegramIntent('Manda un messaggio a Sam su Telegram «ciao bello!»', { office: true }),
    { action: 'send', target: 'Sam', text: 'ciao bello!' });
  assert.equal(telegramIntent('Telegram: Manda un messaggio a Sam «ciao bello»', { office: true })?.action, 'send');
  assert.equal(telegramIntent('Manda un messaggio a Sam "hai installato Telegram?"', { office: true }), null);
  assert.equal(telegramIntent('Manda un messaggio a Sam "ciao" e a Jo "a domani"'), null);
  assert.deepEqual(telegramIntent('Cosa è successo oggi nel privato Sam?'),
    { action: 'read', target: 'Sam', type: 'direct', question: 'Cosa è successo oggi nel privato Sam?' });
});

function fixture() {
  const store = new TelegramStore(':memory:');
  const effects = [];
  const messages = new Map();
  const officeMessages = new Map();
  const officeReactions = new Map();
  let broker;
  let binding;
  let clock = 1_000;
  const resolved = [{ peer: 'user:1001', name: 'Sam Example', room: '!sam:sample.test' }];
  const settings = { domain, owner, agent, officeRoom, controlRoom };
  const options = { store, settings, now: () => clock, checkRoom: async () => true,
    checkOffice: async () => true,
    getBinding: async () => binding,
    guide: async input => {
      if (input.action === 'resolve' || input.action === 'list') return { ok: true, rooms: resolved };
      effects.push(input);
      return { ok: true, message: 'Private details for ' + input.target };
    },
    post: async (txn, content) => {
      if (!messages.has(txn)) messages.set(txn, { id: '$msg' + (messages.size + 1), content });
      return messages.get(txn).id;
    },
    postOffice: async (txn, content) => officeMessages.set(txn, content),
    reactOffice: async (txn, event, key) => {
      officeReactions.set(txn, { event, key });
      return txn;
    },
    clearOfficeReaction: async (_txn, id) => officeReactions.delete(id),
    runAgent: async (grant, tool) => {
      assert.deepEqual(await broker.tool({ grant: 'forged', tool }), { status: 'forbidden' });
      assert.deepEqual(await broker.tool({ grant, tool: tool === 'open_room' ? 'unlink_room' : 'open_room' }), { status: 'forbidden' });
      await broker.tool({ grant, tool });
      assert.deepEqual(await broker.tool({ grant, tool }), { status: 'forbidden' });
    },
  };
  broker = createTelegramDelegation(options);
  return { store, effects, messages, officeMessages, officeReactions, resolved, broker: () => broker,
    restart: () => { broker = createTelegramDelegation(options); },
    setBinding: value => { binding = value; }, advance: amount => { clock += amount; } };
}

test('an Office handoff is private, idempotent and selects one verified group', async () => {
  const f = fixture();
  f.resolved.push({ peer: 'channel:222', name: 'Project Room', room: null });
  const request = { ...caller, id: '$office', method: 'submit', text: 'add the Project Room group to my Telegram rooms' };
  assert.equal((await f.broker().office({ ...request, actor: '@coder:sample.test' })).status, 'forbidden');
  assert.equal((await f.broker().office({ ...request, room: '!other:sample.test' })).status, 'forbidden');
  assert.equal((await f.broker().office(request)).status, 'pending');
  await f.broker().tick();
  f.restart();
  await f.broker().tick();
  assert.deepEqual(f.effects, [{ id: '$office', action: 'add', target: 'channel:222' }]);
  assert.deepEqual(await f.broker().office(request), { status: 'completed', link: 'https://matrix.to/#/!telegram%3Asample.test/%24msg1' });
  assert.equal((await f.broker().office({ ...request, text: 'Add Sam to my Telegram rooms' })).status, 'conflict');
  assert.equal([...f.messages.values()].at(-1).content.body, 'Private details for channel:222');
});

test('a direct Office mention keeps Telegram details private and acknowledges the original assignment', async () => {
  const f = fixture();
  const request = direct('$office-list', 'List my Telegram rooms');
  assert.equal(await f.broker().directOffice(direct('$untrusted', 'List my Telegram rooms', '@coder:sample.test')), false);
  assert.equal(await f.broker().directOffice({ ...request, content: { ...request.content, 'm.mentions': {} } }), false);
  await f.broker().directOffice(request);
  await f.broker().tick();
  await f.broker().publishOffice();
  assert.deepEqual([...f.officeReactions.values()], [{ event: request.event_id, key: '👀' }]);
  f.restart();
  await f.broker().directOffice(request);
  await f.broker().tick();
  await f.broker().publishOffice();
  await f.broker().publishOffice();
  assert.equal(f.officeMessages.size, 2);
  assert.deepEqual([...f.officeReactions.values()], [{ event: request.event_id, key: '✅' }]);
  for (const message of f.officeMessages.values()) {
    assert.equal(message['m.relates_to'].event_id, request.event_id);
    assert.match(message.body, /https:\/\/matrix\.to\/#\/!telegram%3Asample\.test/u);
    assert.doesNotMatch(message.body, /Sam Example|!sam/u);
  }
  assert.match([...f.messages.values()].at(-1).content.body, /Sam Example/u);
});

test('Task Manager can relay a Dot assignment to queue one operator-approved Telegram send', async () => {
  const f = fixture();
  const binding = { id: 'sam', room: '!sam:sample.test', generation: 1, revision: 1 };
  f.setBinding(binding);
  const planner = '@dot:sample.test';
  const name = 'Task Manager';
  const text = 'Telegram: invia un messaggio privato a Sam Example «ciao»';
  const target = { id: caller.actor, name };
  const event = { room_id: officeRoom, event_id: '$dot-send', sender: planner, type: 'm.room.message', origin_server_ts: 1,
    content: { ...mentionMessage('Saluto privato\n\n' + text, target), [dotPlanKey]: { task_id: 'telegram-smoke' } } };
  const context = { owner, planner, name, userId: caller.actor, roomId: officeRoom, agents: [caller.actor], activatedAt: 0 };
  assert.equal(acceptedMessage(event, context)?.id, event.event_id);
  assert.equal(telegramOfficeAssignment(event, { ...context, planner: undefined }), null);
  assert.equal(telegramOfficeAssignment({ ...event, content: { ...event.content, [dotPlanKey]: undefined } }, context), null);
  assert.equal(telegramOfficeAssignment({ ...event, content: { ...event.content,
    body: 'Task Manager Saluto privato\n\nInvia il testo esatto «ciao» alla chat privata di Sam Example tramite l’integrazione Matrix.' } }, context), null);
  assert.deepEqual(telegramOfficeAssignment(event, context), { sender: planner, text });
  const request = { ...caller, ...telegramOfficeAssignment(event, context), id: event.event_id, method: 'submit' };
  assert.equal((await f.broker().office({ ...request, sender: '@someone:sample.test' })).status, 'forbidden');
  assert.equal((await f.broker().office(request)).status, 'pending');
  await f.broker().tick();
  await f.broker().office(request);
  await f.broker().tick();
  const drafts = f.store.all('SELECT payload,requires_dot FROM control_request');
  assert.equal(drafts.length, 1);
  assert.equal(drafts[0].requires_dot, 0);
  assert.deepEqual(JSON.parse(drafts[0].payload), { action: 'compose', destination: 'sam', verifiedBinding: binding,
    chatType: 'direct', text: 'ciao' });
  assert.equal((await f.broker().office({ ...request, method: 'status' })).status, 'pending');
  assert.equal(f.effects.length, 0);
  const receipt = await f.broker().office({ ...request, method: 'status' });
  assert.deepEqual(receipt, { status: 'pending' });
  assert.equal(telegramReceipt(receipt), null);
  const poll = { status: 'awaiting_approval', link: 'https://matrix.to/#/!telegram%3Asample.test/%24poll' };
  assert.deepEqual(telegramReceipt(poll), poll);
  assert.equal(telegramReceipt({ ...receipt, link: 'https://example.test/private' }), null);
  assert.equal(telegramReceipt({ status: 'completed' }), null);
});

test('a structured Dot send survives multiline Office prose and preserves the exact message', async () => {
  const f = fixture();
  const binding = { id: 'sam', room: '!sam:sample.test', generation: 1, revision: 1 };
  f.setBinding(binding);
  const planner = '@dot:sample.test';
  const target = { id: caller.actor, name: 'Task Manager' };
  const telegram = { target: 'Sam Example', type: 'direct', text: 'Ciao «Sam»!\nA domani.' };
  const content = { ...mentionMessage('Telegram request\n\nPrepare this for review.\nReport progress in Office.', target),
    [dotPlanKey]: { task_id: 'telegram-structured', telegram } };
  const context = { owner, planner, userId: target.id, name: target.name };
  const assignment = telegramOfficeAssignment({ sender: planner, content }, context);
  assert.deepEqual(assignment, { sender: planner, telegram });
  assert.equal(telegramOfficeAssignment({ sender: '@someone:sample.test', content }, context), null);
  assert.equal(telegramOfficeAssignment({ sender: planner, content: { ...content, 'm.mentions': {} } }, context), null);
  const input = { ...caller, ...assignment, id: '$structured', method: 'submit' };
  assert.equal((await f.broker().office({ ...input, sender: owner })).status, 'forbidden');
  assert.equal((await f.broker().office(input)).status, 'pending');
  assert.equal((await f.broker().office(input)).status, 'pending');
  await f.broker().tick();
  assert.equal(f.messages.size, 0, 'A verified Office send should queue a draft without a Bridge placeholder');
  const drafts = f.store.all('SELECT payload FROM control_request');
  assert.deepEqual(drafts.map(({ payload }) => JSON.parse(payload)), [{ action: 'compose', destination: 'sam',
    verifiedBinding: binding, chatType: 'direct', text: telegram.text }]);
  assert.equal(f.effects.length, 0);
});

test('Telegram Agent accepts a direct mention with the exact text before the named private chat', async () => {
  const f = fixture();
  f.setBinding({ id: 'sam', room: '!sam:sample.test', generation: 1, revision: 1 });
  const request = direct('$private-literal', 'Invia una sola volta il testo esatto «ciao!» alla chat privata già autorizzata e verificata di Sam Example.');
  assert.equal(await f.broker().directOffice(request), true);
  await f.broker().tick();
  const draft = f.store.get('SELECT payload FROM control_request');
  assert.equal(draft && JSON.parse(draft.payload).text, 'ciao!');
  assert.equal(f.effects.length, 0);
});

test('the private mention works without Dot and disambiguates only in its own thread', async () => {
  const f = fixture();
  f.resolved.push({ peer: 'user:1002', name: 'Sam Example', room: '!other:sample.test' });
  const request = direct('$direct', 'add Sam Example to my Telegram rooms.');
  assert.equal(await f.broker().direct({ ...request, sender: '@dot:sample.test' }), false);
  assert.equal(await f.broker().direct({ ...request, content: { ...request.content, 'm.mentions': {} } }), false);
  assert.equal(await f.broker().direct(request), true);
  await f.broker().tick();
  await f.broker().tick();
  assert.equal(f.effects.length, 0);
  const select = (thread, sender = owner) => ({ ...direct('$select', '', sender), content: { msgtype: 'm.text',
    body: 'telegram choose 2', 'm.relates_to': { rel_type: 'm.thread', event_id: thread } } });
  await f.broker().direct(select('$different'));
  await f.broker().direct(select('$direct', '@dot:sample.test'));
  await f.broker().tick();
  assert.equal(f.effects.length, 0);
  f.restart();
  await f.broker().direct(select('$direct'));
  await f.broker().tick();
  await f.broker().tick();
  assert.equal(f.effects[0].target, 'user:1002');
  assert.equal(f.effects.length, 1);
});

test('a list request keeps linked-room links private and does not append a missing event ID', async () => {
  const f = fixture();
  const request = { ...caller, id: '$list', method: 'submit', text: 'List my Telegram rooms' };
  await f.broker().office(request);
  await f.broker().tick();
  await f.broker().tick();
  assert.equal([...f.messages.values()].at(-1).content.body, '• Sam Example · https://matrix.to/#/!sam%3Asample.test');
  assert.deepEqual(Object.keys(await f.broker().office(request)).sort(), ['link', 'status']);
});

test('requests require a binding and remain pending until the poll and delivery reach a terminal outcome', async () => {
  const f = fixture();
  const postDraft = id => f.broker().direct(direct(id, 'prepare a private message to Sam Example saying tomorrow works.'));
  await postDraft('$unbound');
  await f.broker().tick();
  await f.broker().tick();
  assert.match([...f.messages.values()][0].content.body, /verified binding/u);
  const binding = { id: 'sam', room: '!sam:sample.test', generation: 1, revision: 1 };
  f.setBinding(binding);
  await postDraft('$draft');
  await f.broker().tick();
  const work = f.store.get('SELECT payload,requires_dot FROM control_request WHERE event=?', '$draft');
  assert.equal(work.requires_dot, 0);
  assert.deepEqual(JSON.parse(work.payload), { action: 'compose', destination: 'sam', verifiedBinding: binding,
    chatType: 'direct', text: 'tomorrow works.' });
  assert.equal(draftReceipt({ state: 'awaiting_approval' }, null, 'draft'), null);
  assert.equal(draftReceipt({ state: 'awaiting_approval' }, {}), 'awaiting_approval');
  assert.equal(draftReceipt({ state: 'cancelled' }, { decision: 'reject' }), 'declined');
  assert.equal(draftReceipt({ state: 'matrix_accepted' }, {}, 'send'), 'sending');
  assert.equal(draftReceipt({ state: 'cancelled' }, {}, 'send'), 'blocked');
  assert.equal(draftReceipt({ state: 'unknown' }, {}, 'send'), 'unconfirmed');
  assert.equal(draftReceipt({ state: 'bridge_confirmed' }, {}, 'send'), 'completed');
});

test('an unclear Office mention gets one actionable reply in Office and no Bridge thread', async () => {
  const f = fixture();
  const event = direct('$unclear-office', 'Do these two unrelated things.');
  assert.equal(await f.broker().directOffice(event), true);
  await f.broker().tick();
  await f.broker().tick();
  await f.broker().publishOffice();
  await f.broker().publishOffice();
  assert.equal(f.messages.size, 0);
  assert.equal(f.officeMessages.size, 1);
  const [reply] = f.officeMessages.values();
  assert.equal(reply['m.relates_to'].event_id, event.event_id);
  assert.match(reply.body, /one Telegram chat and one action/u);
  assert.deepEqual([...f.officeReactions.values()], [{ event: event.event_id, key: '❌' }]);
});

test('a short Italian send request queues one approval draft and does not deliver it', async () => {
  const f = fixture();
  f.setBinding({ id: 'sam', room: '!sam:sample.test', generation: 1, revision: 1 });
  const event = direct('$short-request', 'manda messaggio a Sam "ciao bello"');
  assert.equal(await f.broker().direct(event), true);
  assert.equal(await f.broker().direct(event), true);
  await f.broker().tick();
  const queued = f.store.all('SELECT payload FROM control_request WHERE event=?', event.event_id);
  assert.equal(queued.length, 1);
  assert.equal(JSON.parse(queued[0].payload).text, 'ciao bello');
  assert.deepEqual(f.effects, []);
});
