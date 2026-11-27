import test from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { createHireConfiguration, hiringMarkdown } from './hire-configuration.mjs';

const initial = { kind: 'add', agent: 'researcher', web: true, firstTask: 'Compare two sources', profile: {
  name: 'Researcher', key: 'researcher', model: 'suggested', effort: 'high',
  profile: { persona: { purpose: 'Research', duties: ['Compare sources'], working_style: 'Cite claims' },
    limits: 'Public material', contacts: ['task-manager'],
    capabilities: [{ code: 'research', description: 'Find evidence', resources: ['task:*'] }] },
} };

test('a hire resumes before its poll and binds the exact connection, model, effort and tools', async () => {
  const db = new DatabaseSync(':memory:');
  const request = '$private-source';
  const prompts = [];
  const access = async ({ action, account }) => ({
    accounts: { accounts: [{ id: 'plan-fixture', mode: 'chatgpt_plan', label: 'My Plan', ready: true }] },
    catalog: { models: [{ id: 'live-model', efforts: ['low', 'high'] }] },
    tools: { tools: [{ id: 'public-research', purpose: 'public-web', version: '1', methods: ['search'], permissions: 'Read public pages' }] },
  })[action];
  const start = () => createHireConfiguration({ db, access, send: async (id, content) => {
    prompts.push({ id, content });
    return '$prompt' + prompts.length;
  } });
  const row = { request, initiated: 'agent', summary: 'Requested in Office' };
  let setup = start();
  assert.equal(await setup.prepare(row, initial), null);
  assert.match(prompts.at(-1).content.body, /ChatGPT Codex[\s\S]*ChatGPT Plan/);
  const select = (n, parent = '$prompt' + prompts.length) => setup.accept({
    type: 'm.room.message',
    content: { msgtype: 'm.text', body: '!hire ' + n, 'm.relates_to': { 'm.in_reply_to': { event_id: parent } } },
  }, { event_id: '$choice' + prompts.length });
  assert.match(await select(2, '$unrelated'), /current hiring setup message/);
  await select(2);
  setup = start();
  assert.equal(await setup.prepare(row, initial), null);
  assert.match(prompts.at(-1).content.body, /My Plan/);
  await select(1);
  await setup.prepare(row, initial);
  assert.match(prompts.at(-1).content.body, /live-model/);
  await select(1);
  await setup.prepare(row, initial);
  assert.match(prompts.at(-1).content.body, /high/);
  await select(2);
  await setup.prepare(row, initial);
  const operation = await setup.prepare(row, initial);
  assert.equal(operation.provider.account, 'plan-fixture');
  assert.equal(operation.provider.mode, 'chatgpt_plan');
  assert.equal(operation.profile.model, 'live-model');
  assert.equal(operation.profile.effort, 'high');
  assert.deepEqual(operation.tools.map(tool => tool.id), ['public-research']);
  const markdown = hiringMarkdown(operation, row.summary);
  assert.match(markdown, /Compare two sources/);
  assert.match(markdown, /search/);
  assert.match(markdown, /task-manager/);
});
