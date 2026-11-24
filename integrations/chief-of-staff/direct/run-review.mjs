import { readFile, writeFile } from 'node:fs/promises';
import { runTurn } from '../../../agents/connector/instance-acp.mjs';
import { parseObservation, validateRoomId } from '../observation.mjs';
import { reportId } from './review.mjs';
import { readInteractions, reviewPrompt, reviewThreads } from './evidence.mjs';

const room = validateRoomId((await readFile('/direct/agents-room-id', 'utf8')).trim());
const reader = (await readFile('/reader-token', 'utf8')).trim();
const threads = reviewThreads(await readInteractions(reader, room), room);
if (!threads.length) {
  console.log('Context Intelligence has no Office threads to review yet.');
} else {
  const response = await runTurn({
    url: process.env.INSTANCE_ACP_URL, workspace: '/workspace', revision: '0.22.17',
    token: '/acp/credential-token', ca: '/acp/ca.pem', turnTimeoutMs: 160000,
  }, null, reviewPrompt(threads), () => {});
  if (parseObservation(response, threads)) {
    const id = reportId(threads);
    const file = '/direct/outbox/' + id + '.json';
    await writeFile(file, JSON.stringify({ room, threads, response, created_at: new Date().toISOString() }),
      { flag: 'wx', mode: 0o600 }).catch(error => { if (error.code !== 'EEXIST') throw error; });
    console.log('Chief of Staff review queued for its direct chat.');
  } else {
    console.log('The observed interactions did not support a proposal.');
  }
}
