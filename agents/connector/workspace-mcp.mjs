import { lstat, mkdir, opendir, realpath } from 'node:fs/promises';
import { basename, dirname, join, relative } from 'node:path';
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';
import { registerTeamTools } from './team-tools.mjs';
import { registerGitHubTools } from './github-tools.mjs';
import { inspectAttachment } from './attachment-reader.mjs';
import { FileTooLarge, readWorkspaceBytes, workspacePath, writeWorkspaceFile } from './workspace-paths.mjs';

const root = await realpath(process.env.MATRIX_WORKSPACE);
const contentLimit = 131072;

function message(text, isError = false) {
  return { isError, content: [{ type: 'text', text }] };
}

function report(error) {
  if (error.code === 'ENOENT') return message('Workspace file or parent directory does not exist.', true);
  return message(error.code ? 'The workspace operation failed.' : error.message, true);
}

const server = new McpServer({ name: 'matrix-workspace-files', version: '1.0.0' });
const repository = process.env.MATRIX_REPOSITORY_DRAFTS === 'true';
if (!repository) registerTeamTools(server);
const path = z.string().min(1).max(1024);
server.registerTool('list_files', {
  description: 'List files and directories under a relative workspace directory. Defaults to the workspace root. Returns paths and modification times, newest first.',
  inputSchema: { directory: path.optional() },
  annotations: { readOnlyHint: true },
}, async ({ directory = '.' }) => {
  try {
    const target = workspacePath(root, directory);
    if (await realpath(target) !== target) throw new Error('Symbolic links are unavailable.');
    const listing = await opendir(target);
    const entries = [];
    const scanLimit = 1000;
    const resultLimit = 50;
    let scanned = 0;
    for await (const entry of listing) {
      if (scanned++ >= scanLimit) break;
      if (!entry.isFile() && !entry.isDirectory()) continue;
      const entryPath = join(target, entry.name);
      const stat = await lstat(entryPath).catch(error => {
        if (error.code !== 'ENOENT') throw error;
      });
      if (!stat || (!stat.isDirectory() && !(stat.isFile() && stat.nlink === 1))) continue;
      entries.push({
        path: relative(root, entryPath),
        type: stat.isDirectory() ? 'directory' : 'file',
        modified_at: stat.mtime.toISOString(),
      });
    }
    entries.sort((first, second) => second.modified_at.localeCompare(first.modified_at) || first.path.localeCompare(second.path));
    return message(JSON.stringify({
      entries: entries.slice(0, resultLimit),
      truncated: scanned > scanLimit || entries.length > resultLimit,
    }));
  } catch (error) {
    if (error.code === 'ENOTDIR') return message('The workspace path is not a directory.', true);
    return report(error);
  }
});

server.registerTool('read_file', {
  description: 'Read a UTF-8 text file from this agent’s workspace using a relative path.',
  inputSchema: { path },
  annotations: { readOnlyHint: true },
}, async ({ path }) => {
  try {
    return message((await readWorkspaceBytes(root, path, contentLimit)).toString('utf8'));
  } catch (error) {
    if (error instanceof FileTooLarge) return message('File exceeds 128 KiB. Use read_attachment for larger text files.', true);
    return report(error);
  }
});

server.registerTool('read_attachment', {
  description: 'Inspect a Matrix attachment by its relative workspace path. Reads UTF-8, PDF or DOCX text, or a PNG, JPEG, or WebP image. Call again with next_offset to continue text.',
  inputSchema: { path, offset: z.number().int().nonnegative().optional() },
  annotations: { readOnlyHint: true },
}, async ({ path, offset }) => {
  try {
    const data = await readWorkspaceBytes(root, path, Number(process.env.MATRIX_MEDIA_LIMIT) || 10 * 1024 * 1024);
    return { content: await inspectAttachment(data, path, offset) };
  } catch (error) {
    return report(error);
  }
});

server.registerTool('write_file', {
  description: 'Create or replace a UTF-8 text file in this agent’s workspace using a relative path.',
  inputSchema: { path, text: z.string().max(contentLimit) },
}, async ({ path, text }) => {
  try {
    if (Buffer.byteLength(text) > contentLimit) return message('Text exceeds 128 KiB.', true);
    const target = await writeWorkspaceFile(root, path, text);
    return message('Saved ' + basename(target) + ' in the workspace.');
  } catch (error) {
    return report(error);
  }
});

if (process.env.AGENT_USERNAME === 'coder') {
  server.registerTool('create_directory', {
    description: 'Create a directory inside the workspace by relative path. Create the parent first when needed.',
    inputSchema: { path },
  }, async ({ path }) => {
    try {
      const target = workspacePath(root, path);
      const parent = dirname(target);
      if (target !== root) {
        if (await realpath(parent) !== parent) throw new Error('Symbolic links are unavailable.');
        await mkdir(target, { mode: 0o700 }).catch(error => { if (error.code !== 'EEXIST') throw error; });
        if (!(await lstat(target)).isDirectory()) throw new Error('Use a workspace directory path.');
      }
      return message(`Workspace directory ready: ${relative(root, target) || '.'}.`);
    } catch (error) {
      return report(error);
    }
  });
  if (!repository && !process.env.MATRIX_CATALOG_PATH) await registerGitHubTools(server);
}

await server.connect(new StdioServerTransport());
