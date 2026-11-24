import { PassThrough, Readable, Writable } from 'node:stream';
import { once } from 'node:events';
import { readFile } from 'node:fs/promises';
import { setTimeout as delay } from 'node:timers/promises';
import * as acp from '@agentclientprotocol/sdk';
import WebSocket from 'ws';

export async function runTurn(engine, previous, prompt, onSession) {
  if (!engine || !/^wss:\/\/[^/]+\/api\/v1\/acp$/u.test(engine.url) || !engine.revision || !engine.workspace) {
    throw new Error('A pinned Constellation Instance ACP endpoint is required.');
  }
  const [token, ca] = await Promise.all([readFile(engine.token, 'utf8'), readFile(engine.ca, 'utf8')]);
  const socket = new WebSocket(engine.url, {
    headers: { authorization: `Bearer ${token.trim()}` }, ca, handshakeTimeout: 10_000,
    followRedirects: false, maxPayload: 1024 * 1024,
  });
  socket.on('error', () => {});
  const input = new PassThrough();
  const output = new PassThrough();
  const decoder = new TextDecoder();
  let pending = '';
  socket.on('message', (data, binary) => {
    if (binary) return socket.close(1003);
    input.write(data);
    input.write('\n');
  });
  socket.on('close', () => input.end());
  output.on('data', (chunk) => {
    pending += decoder.decode(chunk, { stream: true });
    for (let eol = pending.indexOf('\n'); eol !== -1; eol = pending.indexOf('\n')) {
      const line = pending.slice(0, eol);
      pending = pending.slice(eol + 1);
      if (line && socket.readyState === WebSocket.OPEN) socket.send(line);
    }
  });
  const controller = new AbortController();
  try {
    await once(socket, 'open', { signal: AbortSignal.timeout(11_000) });
    let answer = '';
    let active;
    let observedTool = false;
    const run = acp.client({ name: 'matrix-root-gateway' })
      .onRequest(acp.methods.client.session.requestPermission, () => ({ outcome: { outcome: 'cancelled' } }))
      .onNotification(acp.methods.client.session.update, ({ params }) => {
        if (params.sessionId !== active) return;
        const update = params.update;
        if (['tool_call', 'tool_call_update'].includes(update.sessionUpdate)) observedTool = true;
        if (update.sessionUpdate === 'agent_message_chunk' && update.content.type === 'text') {
          answer = (answer + update.content.text).slice(0, 4500);
        }
      }).connectWith(acp.ndJsonStream(Writable.toWeb(output), Readable.toWeb(input)), async (connection) => {
        const methods = acp.methods.agent;
        const hello = await connection.request(methods.initialize, {
          protocolVersion: acp.PROTOCOL_VERSION, clientCapabilities: {},
          clientInfo: { name: 'matrix-root-gateway', version: '0.1.0' },
        });
        if (!hello.agentCapabilities?.loadSession) throw new Error('This backend cannot load saved sessions.');
        const options = { cwd: engine.workspace, mcpServers: [] };
        if (previous) {
          await connection.request(methods.session.load, { ...options, sessionId: previous });
          active = previous;
        } else {
          active = (await connection.request(methods.session.new, options)).sessionId;
        }
        onSession(active);
        const result = await connection.request(methods.session.prompt, { sessionId: active, prompt: [
          { type: 'text', text: prompt },
        ] });
        if (observedTool || result.stopReason !== 'end_turn') throw new Error('ACP did not finish a tool-free turn.');
        return answer.trim();
      });
    return await Promise.race([run, delay(engine.turnTimeoutMs ?? 120_000, null, { signal: controller.signal }).then(() => {
      throw Object.assign(new Error('ACP turn timed out.'), { code: 'ACP_TURN_TIMEOUT' });
    })]);
  } finally {
    controller.abort();
    socket.terminate();
    input.destroy();
    output.destroy();
  }
}
