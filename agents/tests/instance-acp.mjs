import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:https';
import { execFileSync } from 'node:child_process';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { once } from 'node:events';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { runTurn } from '../connector/instance-acp.mjs';
import { executionEvidence } from '../connector/context.mjs';
import { Store } from '../connector/store.mjs';

const imports = createRequire(new URL('../connector/instance-acp.mjs', import.meta.url));
const { WebSocketServer } = imports('ws');

test('ACP saves and reloads a session through the pinned TLS channel', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'instance-acp-'));
  try {
    const ca = join(dir, 'ca.pem');
    const key = join(dir, 'key.pem');
    const token = join(dir, 'token');
    execFileSync('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes',
      '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost',
      '-keyout', key, '-out', ca, '-days', '1'], { stdio: 'ignore' });
    await writeFile(token, 'fixture-credential');
    const server = createServer({ key: await readFile(key), cert: await readFile(ca) });
    const ws = new WebSocketServer({ server, path: '/api/v1/acp' });
    const calls = [];
    ws.on('connection', (socket, upgrade) => {
      assert.equal(upgrade.headers.authorization, 'Bearer fixture-credential');
      socket.on('message', raw => {
        const { method, params, id } = JSON.parse(raw);
        calls.push(method);
        let result = {};
        if (method === 'initialize') result = { protocolVersion: 1, agentCapabilities: { loadSession: true } };
        if (method === 'session/new') result.sessionId = 'session-one';
        if (method === 'session/load' || method === 'session/prompt') assert.equal(params.sessionId, 'session-one');
        if (method === 'session/prompt') {
          socket.send(JSON.stringify({ jsonrpc: '2.0', method: 'session/update', params: {
            sessionId: params.sessionId, update: { sessionUpdate: 'agent_message_chunk', content: { type: 'text', text: 'Fixture reply' } },
          } }));
          result.stopReason = 'end_turn';
        }
        if (id !== undefined) socket.send(JSON.stringify({ jsonrpc: '2.0', id, result }));
      });
    });
    server.listen(0, '127.0.0.1');
    await once(server, 'listening');
    try {
      const engine = { url: `wss://localhost:${server.address().port}/api/v1/acp`, workspace: '/workspace',
        revision: 'fixture', ca, token, turnTimeoutMs: 3000 };
      let session;
      let started = 0;
      assert.equal(await runTurn(engine, null, 'First', id => { session = id; }, () => started++), 'Fixture reply');
      assert.equal(await runTurn(engine, session, 'Second', () => {}, () => started++), 'Fixture reply');
      assert.equal(started, 2);
      assert.equal(calls.filter(call => call === 'session/new').length, 1);
      assert.equal(calls.filter(call => call === 'session/load').length, 1);
    } finally {
      for (const connection of ws.clients) connection.terminate();
      ws.close();
      await new Promise(resolve => server.close(resolve));
    }
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

test('execution metadata and terminal reaction persist with delivery without persisting request text in context', () => {
  const store = new Store(':memory:');
  const event = { id: '$delegation', request: '$root', thread: '$thread', number: 15, body: 'Private content' };
  const evidence = executionEvidence({ roomId: '!office', actor: '@manager:matrix.test', event, sent: '$result', status: 'completed' });
  store.enqueue(event);
  store.startActivity(event.id);
  store.activitySent(event.id, '$eyes');
  store.sent(event.id, '$result', '✅', evidence);
  assert.equal(store.next(), undefined);
  assert.equal(store.nextReactionCleanup().request, '$root');
  assert.equal(store.nextReactionCleanup().activityId, '$eyes');
  assert.equal(JSON.parse(store.nextEvidence().payload).data.execution.sequence, 15);
  assert.doesNotMatch(store.nextEvidence().payload, /Private content/);
  store.outcomeSent(event.id, '$tick');
  store.activityRemoved(event.id);
  store.evidenceSent('$result');
  assert.equal(store.nextReactionCleanup(), undefined);
  assert.equal(store.nextEvidence(), undefined);
  store.db.close();
});
