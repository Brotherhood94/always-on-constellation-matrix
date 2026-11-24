import { constants } from 'node:fs';
import { lstat, open, realpath, rename, unlink } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { dirname, isAbsolute, join, resolve } from 'node:path';

export function workspacePath(root, path) {
  if (isAbsolute(path) || path.includes('\\') || path.includes('\0')) {
    throw new Error('Use a relative workspace path.');
  }
  const target = resolve(root, path);
  if (target !== root && !target.startsWith(root + '/')) throw new Error('Path is outside the workspace.');
  return target;
}

export async function permittedFile(root, path) {
  const target = workspacePath(root, path);
  if (target === root) throw new Error('Provide a file path within the workspace.');
  const parent = dirname(target);
  if (await realpath(parent) !== parent) throw new Error('Symbolic links are unavailable.');
  const stat = await lstat(target).catch(error => {
    if (error.code === 'ENOENT') return undefined;
    throw error;
  });
  if (stat && (!stat.isFile() || stat.nlink !== 1)) throw new Error('Only regular workspace files are supported.');
  return target;
}

export class FileTooLarge extends Error {
  constructor(size) {
    super('File exceeds the size limit.');
    this.size = size;
  }
}

export async function readWorkspaceBytes(root, path, limit) {
  const target = await permittedFile(root, path);
  const file = await open(target, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    const stat = await file.stat();
    if (!stat.isFile() || stat.nlink !== 1) throw new Error('Only regular workspace files are supported.');
    if (stat.size > limit) throw new FileTooLarge(stat.size);
    const data = Buffer.alloc(stat.size + 1);
    let length = 0;
    while (length < data.length) {
      const { bytesRead } = await file.read(data, length, data.length - length, length);
      if (!bytesRead) break;
      length += bytesRead;
    }
    if (length > limit) throw new FileTooLarge(length);
    if (length > stat.size) throw new Error('The file changed while it was being read.');
    return data.subarray(0, length);
  } finally {
    await file.close();
  }
}

export async function writeWorkspaceFile(root, path, content) {
  const target = await permittedFile(root, path);
  const temporary = join(dirname(target), '.' + randomUUID() + '.tmp');
  const file = await open(temporary, 'wx', 0o600);
  try {
    await file.writeFile(content);
    await file.sync();
    await rename(temporary, target);
  } finally {
    await file.close();
    await unlink(temporary).catch(error => { if (error.code !== 'ENOENT') throw error; });
  }
  return target;
}
