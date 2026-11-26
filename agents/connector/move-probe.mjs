import { readFile } from 'node:fs/promises';
import { join } from 'node:path';

const { MATRIX_URL, MATRIX_SERVER_NAME, AGENT_USERNAME, AGENT_STATE } = process.env;
const access = (await readFile(join(AGENT_STATE, 'matrix-token'), 'utf8')).trim();
const room = (await readFile(join(AGENT_STATE, 'room-id'), 'utf8')).trim();
const matrix = async path => {
  const response = await fetch(MATRIX_URL + '/_matrix/client/v3' + path, {
    headers: { authorization: 'Bearer ' + access }, redirect: 'error', signal: AbortSignal.timeout(15000),
  });
  if (!response.ok) throw new Error('Destination Matrix check failed.');
  return response.json();
};
const identity = '@' + AGENT_USERNAME + ':' + MATRIX_SERVER_NAME;
const device = process.env.MOVE_MATRIX_DEVICE ?? 'MATRIX' + AGENT_USERNAME.toUpperCase().replaceAll('-', '');
const who = await matrix('/account/whoami');
if (who.user_id !== identity || who.device_id !== device) throw new Error('Destination Matrix device does not match.');
const rooms = [room];
for (const saved of ['control-room-id', 'helper-room-id']) {
  const path = await readFile(join(AGENT_STATE, saved), 'utf8').catch(error => {
    if (error.code !== 'ENOENT') throw error;
  });
  if (path?.trim()) rooms.push(path.trim());
}
for (const joined of rooms) {
  const events = await matrix('/rooms/' + encodeURIComponent(joined) + '/state');
  const content = (type, key = '') => events.find(row => row.type === type && row.state_key === key)?.content;
  if (content('m.room.encryption')?.algorithm !== 'm.megolm.v1.aes-sha2' ||
      content('m.room.join_rules')?.join_rule !== 'invite' || content('m.room.create')?.['m.federate'] !== false ||
      content('m.room.member', identity)?.membership !== 'join' ||
      content('m.room.member', process.env.MATRIX_OWNER)?.membership !== 'join') throw new Error('Destination room check failed.');
  if (joined !== room || process.env.MOVE_MATRIX_ONLY === 'true') {
    const members = events.filter(event => event.type === 'm.room.member' && event.content?.membership === 'join');
    if (members.some(member => ![identity, process.env.MATRIX_OWNER].includes(member.state_key))) {
      throw new Error('The destination control room has an unexpected member.');
    }
  }
}
if (process.env.MOVE_MATRIX_ONLY !== 'true') {
  if (AGENT_USERNAME !== 'helper') {
    const status = await fetch(new URL('/status', process.env.CONTEXT_INTELLIGENCE_URL), {
      redirect: 'error', signal: AbortSignal.timeout(10000),
    });
    if (!status.ok || !(await status.json()).neo4j_query_connected) throw new Error('Context Intelligence is unavailable from the destination.');
  }
  try {
    await import('./probe.mjs');
  } catch {
    console.error('The agent’s Amplifier provider or session probe failed.');
    process.exitCode = 78;
  }
}
