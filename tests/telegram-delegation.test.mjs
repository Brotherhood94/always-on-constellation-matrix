import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createTelegramDelegation, draftReceipt } from '../integrations/telegram/delegation.mjs';
import { TelegramStore } from '../integrations/telegram/policy.mjs';
import { telegramIntent, telegramMention } from '../agents/connector/telegram-intent.mjs';
import { acceptedMessage } from '../agents/connector/matrix-routing.mjs';

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
  assert.deepEqual(telegramIntent('Cosa è successo oggi nel privato Sam?'),
    { action: 'read', target: 'Sam', type: 'direct', question: 'Cosa è successo oggi nel privato Sam?' });
});

function fixture() {
  const store = new TelegramStore(':memory:');
  const effects = [];
  const messages = new Map();
  let broker;
  let binding;
  let clock = 1_000;
  const resolved = [{ peer: 'user:1001', name: 'Sam Example', room: '!sam:sample.test' }];
  const settings = { domain, owner, agent, officeRoom, controlRoom };
  const options = { store, settings, now: () => clock, checkRoom: async () => true,
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
    runAgent: async (grant, tool) => {
      assert.deepEqual(await broker.tool({ grant: 'forged', tool }), { status: 'forbidden' });
      assert.deepEqual(await broker.tool({ grant, tool: tool === 'open_room' ? 'unlink_room' : 'open_room' }), { status: 'forbidden' });
      await broker.tool({ grant, tool });
      assert.deepEqual(await broker.tool({ grant, tool }), { status: 'forbidden' });
    },
  };
  broker = createTelegramDelegation(options);
  return { store, effects, messages, resolved, broker: () => broker,
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

test('requests require a binding and draft-only and send tasks wait for different receipts', async () => {
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
  assert.equal(draftReceipt({ state: 'awaiting_approval' }, {}, 'draft'), 'completed');
  assert.equal(draftReceipt({ state: 'matrix_accepted' }, {}, 'send'), 'sending');
  assert.equal(draftReceipt({ state: 'cancelled' }, {}, 'send'), 'blocked');
  assert.equal(draftReceipt({ state: 'unknown' }, {}, 'send'), 'unconfirmed');
  assert.equal(draftReceipt({ state: 'bridge_confirmed' }, {}, 'send'), 'completed');
});
