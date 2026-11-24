import assert from 'node:assert/strict';
import { mkdir, mkdtemp, realpath, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath, pathToFileURL } from 'node:url';

const executable = fileURLToPath(new URL('../connector/workspace-mcp.mjs', import.meta.url));
const fixture = await realpath(await mkdtemp(join(tmpdir(), 'matrix-workspace-')));
const workspace = join(fixture, 'workspace');
const state = join(fixture, 'state');
await mkdir(workspace);
await mkdir(state);
await mkdir(join(workspace, 'desktop'));

const require = createRequire(executable);
const { Client } = await import(require.resolve('@modelcontextprotocol/sdk/client/index.js'));
const { StdioClientTransport } = await import(require.resolve('@modelcontextprotocol/sdk/client/stdio.js'));
await writeFile(join(state, 'test-canary.txt'), 'private test data');
await symlink(join(state, 'test-canary.txt'), join(workspace, 'outside.txt'));
await symlink(state, join(workspace, 'outside-directory'));
await mkdir(join(workspace, 'notes'));
await writeFile(join(workspace, 'notes/index.md'), 'Pilot notes');
const client = new Client({ name: 'workspace-smoke-test', version: '1.0.0' });
const transport = new StdioClientTransport({
  command: process.execPath,
  args: [executable],
  env: { MATRIX_WORKSPACE: workspace, AGENT_USERNAME: process.env.AGENT_USERNAME || 'analyst' },
  stderr: 'inherit',
});
await client.connect(transport);
try {
  const write = await client.callTool({ name: 'write_file', arguments: { path: 'report.md', text: 'Pilot report' } });
  assert.equal(write.isError, false);
  const listing = await client.callTool({ name: 'list_files', arguments: {} });
  assert.equal(listing.isError, false, 'The agent must be able to discover a file by listing its workspace');
  const { entries } = JSON.parse(listing.content[0].text);
  assert.deepEqual(entries.map(entry => entry.path).sort(), ['desktop', 'notes', 'report.md']);
  const newestFile = entries.find(entry => entry.type === 'file');
  const read = await client.callTool({ name: 'read_file', arguments: { path: newestFile.path } });
  assert.equal(read.content[0].text, 'Pilot report');
  const attachment = await client.callTool({ name: 'read_attachment', arguments: { path: newestFile.path } });
  assert.deepEqual(JSON.parse(attachment.content[0].text), { text: 'Pilot report', next_offset: null });
  const nested = await client.callTool({ name: 'list_files', arguments: { directory: 'notes' } });
  assert.equal(JSON.parse(nested.content[0].text).entries[0].path, 'notes/index.md');
  for (const path of ['../state/test-canary.txt', join(state, 'test-canary.txt'), 'outside.txt']) {
    const denied = await client.callTool({ name: 'read_file', arguments: { path } });
    assert.equal(denied.isError, true);
    assert.equal((await client.callTool({ name: 'read_attachment', arguments: { path } })).isError, true);
  }
  for (const directory of ['../state', state, 'outside-directory']) {
    const denied = await client.callTool({ name: 'list_files', arguments: { directory } });
    assert.equal(denied.isError, true);
  }
  const shared = await client.callTool({ name: 'write_file', arguments: { path: 'desktop/artifact.md', text: 'Office file' } });
  assert.equal(shared.isError, false);
  assert.equal(await readFile(join(workspace, 'desktop/artifact.md'), 'utf8'), 'Office file');
  const tools = await client.listTools();
  const hasDirectoryTool = tools.tools.some(tool => tool.name === 'create_directory');
  assert.equal(hasDirectoryTool, process.env.AGENT_USERNAME === 'coder');
  if (hasDirectoryTool) {
    for (const path of ['src', 'src', 'src/lib']) {
      const result = await client.callTool({ name: 'create_directory', arguments: { path } });
      assert.equal(result.isError, false);
    }
    for (const path of ['../state/escape', '/state/escape', 'outside-directory', 'outside-directory/escape', 'report.md']) {
      const result = await client.callTool({ name: 'create_directory', arguments: { path } });
      assert.equal(result.isError, true);
    }
    const source = 'export function add(a, b) { return a + b; }\n';
    const write = await client.callTool({ name: 'write_file', arguments: { path: 'src/lib/add.mjs', text: source } });
    assert.equal(write.isError, false);
    assert.equal(await readFile(join(workspace, 'src/lib/add.mjs'), 'utf8'), source);
    const { add } = await import(pathToFileURL(join(workspace, 'src/lib/add.mjs')));
    assert.equal(add(2, 3), 5);
    console.log('Coder created nested directories and a working code file. External paths were denied.');
  }
  console.log('Workspace MCP discovered the saved report, read its contents and denied access outside the workspace.');
} finally {
  await client.close();
  await rm(fixture, { recursive: true, force: true });
}
