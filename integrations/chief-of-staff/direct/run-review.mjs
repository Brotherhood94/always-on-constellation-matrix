import { readFile, writeFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import { runTurn } from '../../../agents/connector/instance-acp.mjs';
import { validateRoomId } from '../observation.mjs';
import { reportId, workshopObservation } from './review.mjs';
import { readInteractions, reviewPrompt, reviewThreads } from './evidence.mjs';

export async function reviewTeam() {
  const room = validateRoomId((await readFile('/direct/agents-room-id', 'utf8')).trim());
  const reader = (await readFile('/reader-token', 'utf8')).trim();
  const workshop = JSON.parse(await readFile('/direct/workshop.json', 'utf8'));
  const threads = reviewThreads(await readInteractions(reader, room), room);
  if (!threads.length) return 'Context Intelligence has no Office threads to review yet.';

  const response = await runTurn({
    url: process.env.INSTANCE_ACP_URL, workspace: '/workspace', revision: '0.22.17',
    token: '/acp/credential-token', ca: '/acp/ca.pem', turnTimeoutMs: 160000,
  }, null, reviewPrompt(threads, workshop), () => {});
  if (!workshopObservation(response, threads, workshop)) {
    return 'The observed interactions did not support a proposal.';
  }
  const id = reportId(threads);
  const file = '/direct/outbox/' + id + '.json';
  try {
    await writeFile(file, JSON.stringify({ room, threads, response, workshop, created_at: new Date().toISOString() }),
      { flag: 'wx', mode: 0o600 });
  } catch (error) {
    if (error.code === 'EEXIST') return 'A proposal for the same Office evidence is already queued.';
    throw error;
  }
  return 'Chief of Staff review queued for its direct chat.';
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) console.log(await reviewTeam());
