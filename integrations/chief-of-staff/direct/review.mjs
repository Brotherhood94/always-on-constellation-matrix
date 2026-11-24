import { link, open, readFile, unlink } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { dirname, join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { digest, parseObservation, validateRoomId } from '../observation.mjs';
import { readInteractions, reviewPrompt, reviewThreads } from './evidence.mjs';

export function isTeamReview(event) {
  return event?.type === 'm.room.message' && event.content?.msgtype === 'm.text' &&
    !event.content['m.relates_to'] && typeof event.content.body === 'string' &&
    event.content.body.trim().toLowerCase() === 'review team';
}

export function reportId(threads) {
  return digest(threads);
}

export function workshopObservation(raw, threads, workshop) {
  const note = parseObservation(raw, threads);
  if (!note) return null;
  const clean = raw.trim().replace(/^\x60\x60\x60(?:json)?\s*([\s\S]*?)\s*\x60\x60\x60$/i, '$1');
  const key = JSON.parse(clean).agent_key;
  if (key === undefined) return note;
  const target = workshop.find(agent => agent.key === key);
  if (!target || !note.subjects.some(id => id.startsWith(`@${key}:`))) {
    throw new Error('A persona proposal must identify a managed agent in the cited evidence');
  }
  return { ...note, agent_key: key, workshop_target: target.target, workshop_revision: target.revision };
}


async function stage(path, value) {
  const temporary = path + '.' + randomUUID() + '.tmp';
  const file = await open(temporary, 'wx', 0o600);
  try {
    await file.writeFile(JSON.stringify(value));
    await file.sync();
    await file.close();
    await link(temporary, path);
    const directory = await open(dirname(path));
    try {
      await directory.sync();
    } finally {
      await directory.close();
    }
  } catch (error) {
    if (error.code !== 'EEXIST') throw error;
  } finally {
    await file.close();
    await unlink(temporary);
  }
}

async function main() {
  const room = validateRoomId((await readFile('/agents-room-id', 'utf8')).trim());
  const sample = '/run-data/evidence.json';
  switch (process.argv[2]) {
    case 'collect': {
      const token = (await readFile('/reader-token', 'utf8')).trim();
      const threads = reviewThreads(await readInteractions(token, room), room);
      if (!threads.length) {
        console.error('There are no readable Agents threads in Context Intelligence yet.');
        process.exitCode = 3;
        return;
      }
      await stage(sample, threads);
      console.log(`Read ${threads.length} Agents threads from Context Intelligence.`);
      break;
    }
    case 'prompt':
      console.log(reviewPrompt(JSON.parse(await readFile(sample, 'utf8'))));
      break;
    case 'queue': {
      const threads = JSON.parse(await readFile(sample, 'utf8'));
      let response = '';
      process.stdin.setEncoding('utf8');
      for await (const part of process.stdin) {
        response += part;
        if (response.length > 16000) throw new Error('Observation exceeds the response limit');
      }
      if (!parseObservation(response, threads)) {
        console.log('The observed interactions do not support a proposal yet.');
        return;
      }
      const id = reportId(threads);
      await stage(join('/outbox', id + '.json'), {
        room, threads, response, created_at: new Date().toISOString(),
      });
      console.log('Chief of Staff observation queued:', id);
      break;
    }
    default:
      throw new Error('Use collect, prompt, or queue');
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    await main();
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
