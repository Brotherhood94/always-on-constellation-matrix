import { createHash } from 'node:crypto';
import { mkdir, realpath } from 'node:fs/promises';
import { basename, extname, join } from 'node:path';
import crypto from '@matrix-org/matrix-sdk-crypto-nodejs';
import matrix from '@vector-im/matrix-bot-sdk';
import { imageType } from './attachment-reader.mjs';
import { mediaTypes } from './matrix-routing.mjs';
import { FileTooLarge, readWorkspaceBytes, writeWorkspaceFile } from './workspace-paths.mjs';

const knownTypes = new Map([
  ['.txt', 'text/plain'], ['.md', 'text/markdown'], ['.csv', 'text/csv'], ['.json', 'application/json'],
  ['.pdf', 'application/pdf'], ['.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'],
  ['.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'],
]);

export function attachmentLimit(value) {
  if (!Number.isSafeInteger(value) || value < 1) throw new Error('Matrix did not advertise a valid attachment limit.');
  return value;
}

class AttachmentRejected extends Error {}

function safeFilename(value) {
  const name = basename(String(value ?? '').replaceAll('\\', '/'))
    .replace(/[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]/g, '_').trim();
  if (!name || name === '.' || name === '..') return 'attachment';
  const extension = extname(name).slice(0, 10);
  return [...name.slice(0, name.length - extension.length)].slice(0, 60 - extension.length).join('') + extension;
}

function sizeLabel(bytes) {
  if (bytes < 1024 * 1024) return `${bytes.toLocaleString('en-US')} bytes`;
  return `${Number((bytes / (1024 * 1024)).toFixed(2))} MiB`;
}

function sizeNotice(name, limit, size) {
  const detail = size > limit ? ` (${sizeLabel(size)})` : '';
  return `Attachment refused: ${name}${detail} exceeds the ${sizeLabel(limit)} limit per file. Try a smaller file.`;
}

function localMediaId(url, serverName) {
  const match = typeof url === 'string' && /^mxc:\/\/([^/]+)\/([A-Za-z0-9_-]{1,255})$/.exec(url);
  if (!match || match[1] !== serverName) throw new AttachmentRejected('Only attachments from this private Matrix server are available.');
  return match[2];
}

async function mediaRequest(client, endpoint, data) {
  const response = await fetch(client.homeserverUrl.replace(/\/$/, '') + endpoint, {
    method: data ? 'POST' : 'GET',
    headers: { Authorization: `Bearer ${client.accessToken}`, ...(data && { 'Content-Type': 'application/octet-stream' }) },
    body: data,
    redirect: 'error',
    signal: AbortSignal.timeout(20000),
  });
  if (response.ok) return response;
  const details = await response.json().catch(() => ({}));
  if (response.status === 413 || details.errcode === 'M_TOO_LARGE') throw new FileTooLarge();
  if (response.status >= 500 || response.status === 429) {
    throw Object.assign(new Error('Matrix media service is temporarily unavailable.'), { body: details });
  }
  throw new AttachmentRejected('Matrix refused access to the attachment or the media was unavailable.');
}

async function readBounded(response, limit) {
  const declared = Number(response.headers.get('content-length'));
  if (declared > limit) {
    await response.body?.cancel();
    throw new FileTooLarge(declared);
  }
  const chunks = [];
  let length = 0;
  for await (const chunk of response.body) {
    length += chunk.byteLength;
    if (length > limit) throw new FileTooLarge(length);
    chunks.push(chunk);
  }
  return Buffer.concat(chunks, length);
}

export async function uploadAttachment(media, path) {
  const { client, workspace, serverName, limit } = media;
  const name = safeFilename(path);
  let data;
  try {
    data = await readWorkspaceBytes(workspace, path, limit);
  } catch (error) {
    return { issue: error instanceof FileTooLarge ? sizeNotice(name, limit, error.size) :
      `Attachment refused: ${name} is not a readable regular file in the agent's workspace.` };
  }
  const { buffer, file } = await client.crypto.encryptMedia(data);
  try {
    const response = await mediaRequest(client, '/_matrix/media/v3/upload', buffer);
    const { content_uri: url } = await response.json();
    localMediaId(url, serverName);
    return { content: { msgtype: 'm.file', body: name,
      info: { size: data.length, mimetype: imageType(data) ?? knownTypes.get(extname(name).toLowerCase()) ?? 'application/octet-stream' },
      file: { ...file, url } } };
  } catch (error) {
    if (error instanceof FileTooLarge) return { issue: sizeNotice(name, limit, data.length) };
    if (error instanceof AttachmentRejected) return { issue: `Attachment refused: ${name}. ${error.message}` };
    throw error;
  }
}

async function importOne(media, event, ref) {
  const { client, roomId, workspace, serverName, limit, senders } = media;
  let name = 'the requested attachment';
  try {
    const raw = await client.getRawEvent(roomId, ref.id).catch(error => {
      if (!['M_NOT_FOUND', 'M_FORBIDDEN'].includes(error.body?.errcode)) throw error;
    });
    if (!raw || !senders.has(raw.sender)) {
      if (ref.required) throw new AttachmentRejected('The referenced attachment is unavailable in this room.');
      return;
    }
    if (raw.type !== 'm.room.encrypted') {
      if (ref.required || mediaTypes.has(raw.content?.msgtype)) throw new AttachmentRejected('The attachment message was not encrypted.');
      return;
    }
    let decrypted;
    try {
      decrypted = (await client.crypto.decryptRoomEvent(new matrix.EncryptedRoomEvent(raw), roomId)).raw;
    } catch {
      if (ref.required) throw new AttachmentRejected('The agent could not decrypt the attachment message.');
      return;
    }
    const content = decrypted.content;
    if (decrypted.type !== 'm.room.message' || !mediaTypes.has(content?.msgtype)) {
      if (ref.required) throw new AttachmentRejected('The referenced message has no usable attachment.');
      return;
    }
    name = safeFilename(content.filename ?? content.body);
    const relation = content['m.relates_to'];
    if (event.delegator && (raw.sender !== event.delegator || relation?.rel_type !== 'm.thread' || relation.event_id !== event.thread)) {
      throw new AttachmentRejected('The handoff attachment is outside the sender’s assignment thread.');
    }
    if (!content.file) throw new AttachmentRejected('The attachment did not include an encrypted file.');
    if (Number.isSafeInteger(content.info?.size) && content.info.size > limit) throw new FileTooLarge(content.info.size);
    const id = localMediaId(content.file.url, serverName);
    const source = `/_matrix/client/v1/media/download/${encodeURIComponent(serverName)}/${encodeURIComponent(id)}?allow_redirect=false`;
    const ciphertext = await readBounded(await mediaRequest(client, source), limit);
    let plain;
    try {
      plain = crypto.Attachment.decrypt(new crypto.EncryptedAttachment(ciphertext, JSON.stringify(content.file)));
    } catch {
      throw new AttachmentRejected('The encrypted file failed its integrity check or could not be decrypted.');
    }
    const folder = '.matrix-attachments';
    const slot = createHash('sha256').update(ref.id).digest('hex');
    for (const directory of [join(workspace, folder), join(workspace, folder, slot)]) {
      await mkdir(directory, { mode: 0o700 }).catch(error => { if (error.code !== 'EEXIST') throw error; });
      if (await realpath(directory) !== directory) throw new AttachmentRejected('The destination is not a regular workspace directory.');
    }
    const path = join(folder, slot, name);
    await writeWorkspaceFile(workspace, path, plain);
    return { path };
  } catch (error) {
    if (error instanceof FileTooLarge) return { issue: sizeNotice(name, limit, error.size) };
    if (error instanceof AttachmentRejected) return { issue: `Cannot read ${name}. ${error.message}` };
    throw error;
  }
}

export async function importAttachments(media, event) {
  const paths = [];
  const issues = [];
  for (const ref of event.attachmentRefs) {
    const item = await importOne(media, event, ref);
    if (item?.path) paths.push(item.path);
    if (item?.issue) issues.push(item.issue);
  }
  return { paths, issues };
}
