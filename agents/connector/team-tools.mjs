import { execFile } from 'node:child_process';
import { createHash } from 'node:crypto';
import { lookup } from 'node:dns/promises';
import { readFile } from 'node:fs/promises';
import https from 'node:https';
import { BlockList } from 'node:net';
import { promisify } from 'node:util';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import { z } from 'zod';

const run = promisify(execFile);
const blocked = new BlockList();
for (const [address, bits] of [['0.0.0.0',8],['10.0.0.0',8],['100.64.0.0',10],['127.0.0.0',8],
  ['169.254.0.0',16],['172.16.0.0',12],['192.0.0.0',24],['192.0.2.0',24],['192.88.99.0',24],
  ['192.168.0.0',16],['198.18.0.0',15],['198.51.100.0',24],['203.0.113.0',24],['224.0.0.0',3]]) blocked.addSubnet(address, bits);

export function publicUrl(input) {
  const url = new URL(input);
  if (url.protocol !== 'https:' || url.username || url.password || url.port || !url.hostname.includes('.') || url.hash)
    throw new Error('A public HTTPS publisher address is required.');
  return url;
}

// Pin the checked address for the actual TLS connection. Fetches never follow redirects.
export async function publicFetch(input, options = {}) {
  const url = publicUrl(input.toString());
  const addresses = await lookup(url.hostname, {family:4, all:true});
  if (!addresses.length || addresses.some(({address}) => blocked.check(address))) throw new Error('Private destinations are unavailable.');
  const headers = Object.fromEntries(new Headers(options.headers).entries());
  return new Promise((resolve, reject) => {
    const req = https.request(url, {method:options.method || 'GET', headers:{'user-agent':'Constellation-Scouter', ...headers},
      family:4, autoSelectFamily:false, lookup:(_name, _opts, cb) => cb(null, addresses[0].address, 4),
      signal:AbortSignal.timeout(12000)}, res => {
      const chunks = [];
      let size = 0;
      res.on('error', reject);
      res.on('data', chunk => {
        size += chunk.length;
        if (size > 512000) req.destroy(new Error('Publisher response is too large.'));
        else chunks.push(chunk);
      });
      res.on('end', () => {
        if (res.statusCode >= 300 && res.statusCode < 400) return reject(new Error('Publisher redirected the request.'));
        resolve(new Response([204,205,304].includes(res.statusCode) ? null : Buffer.concat(chunks),
          {status:res.statusCode, headers:res.headers}));
      });
    });
    req.on('error', reject);
    req.end(options.body);
  });
}

export async function research(topic, fetcher = publicFetch) {
  const query = topic.trim().slice(0,100);
  if (!/^[\p{L}\p{N} _.-]{2,100}$/u.test(query)) throw new Error('Use public topic keywords.');
  const urls = ['https://api.github.com/search/repositories?per_page=4&q=' + encodeURIComponent(query),
    'https://registry.modelcontextprotocol.io/v0.1/servers?limit=5&search=' + encodeURIComponent(query)];
  const results = await Promise.allSettled(urls.map(async (url, i) => {
    const response = await fetcher(url);
    if (!response.ok) throw new Error('Publisher unavailable.');
    const data = await response.json();
    if (!i) return data.items.map(row => ({name:row.full_name, source:row.html_url, summary:row.description?.slice(0,300)}));
    return data.servers.map(({server}) => ({name:server.name, version:server.version, source:server.repository?.url,
      summary:server.description?.slice(0,300), endpoints:server.remotes?.filter(r => r.type === 'streamable-http').map(r => r.url)}));
  }));
  if (results.every(result => result.status === 'rejected')) throw new Error('Public sources are temporarily unavailable.');
  return results.flatMap(result => result.status === 'fulfilled' ? result.value : []);
}

async function github(tool, action, deps) {
  const token = (await deps.read('/tool-credentials/github-token','utf8')).trim();
  if (!token) throw new Error('GitHub sign-in required.');
  const suffix = {verify:'', repository:'', issues:'/issues?per_page=20', pulls:'/pulls?per_page=20'}[action];
  if (suffix === undefined) throw new Error('Choose repository, issues, or pulls.');
  const {stdout} = await deps.run('/usr/bin/gh', ['api','--method','GET','--hostname','github.com','--','repos/' + tool.repository + suffix],
    {env:{PATH:'/usr/bin:/bin', HOME:'/tmp', GH_TOKEN:token, GH_PROMPT_DISABLED:'1', GH_NO_UPDATE_NOTIFIER:'1'},
      timeout:12000,maxBuffer:512000});
  return JSON.parse(stdout);
}

export async function useTool(tool, action = 'verify', args = {}, overrides = {}) {
  const deps = {read:readFile, run, fetch:publicFetch, ...overrides};
  if (tool.kind === 'github' && /^[\w.-]+\/[\w.-]+$/u.test(tool.repository) && !tool.repository.includes('..')) {
    return github(tool, action, deps);
  }
  if (tool.kind === 'skill' && ['verify','read'].includes(action)) {
    const response = await deps.fetch(tool.source);
    if (!response.ok) throw new Error('Could not retrieve the skill.');
    const bytes = Buffer.from(await response.arrayBuffer());
    if (createHash('sha256').update(bytes).digest('hex') !== tool.sha256) throw new Error('Skill digest changed.');
    return action === 'read' ? bytes.toString('utf8').slice(0,32000) : 'Skill digest verified';
  }
  if (tool.kind === 'mcp' && (action === 'verify' || tool.methods.includes(action))) {
    const client = new Client({name:'constellation-office',version:'1.0.0'});
    try {
      const endpoint = publicUrl(tool.endpoint);
      const transport = new StreamableHTTPClientTransport(endpoint, {fetch: (input, opts) => {
        if (new URL(input).origin !== endpoint.origin) throw new Error('MCP origin changed.');
        return deps.fetch(input, opts);
      }});
      await client.connect(transport);
      if (client.getServerVersion()?.version !== tool.version) throw new Error('MCP server version changed.');
      const available = (await client.listTools()).tools.map(item => item.name);
      if (!tool.methods.every(name => available.includes(name))) throw new Error('MCP methods changed.');
      return action === 'verify' ? available : await client.callTool({name:action,arguments:args});
    } finally { await client.close(); }
  }
  throw new Error('The requested operation is outside this tool grant.');
}

export async function targetCatalog() {
  const source = process.env.MATRIX_CATALOG_PATH;
  let contents;
  if (source?.startsWith('/')) contents = JSON.parse(await readFile(source,'utf8'));
  else {
    const url = new URL(source);
    if (url.protocol !== 'https:' || url.hostname !== process.env.MATRIX_SERVER_NAME || url.pathname !== '/agents.json' ||
        url.username || url.password || url.search || url.hash) throw new Error('Invalid private catalog.');
    const res = await fetch(url,{redirect:'error',cache:'no-store',signal:AbortSignal.timeout(6000)});
    if (!res.ok) throw new Error('Private catalog unavailable.');
    contents = await res.json();
  }
  if (!Number.isFinite(contents.time) || Math.abs(Date.now()/1000 - contents.time) > 180) throw new Error('Tool catalog is stale.');
  const agent = contents.agents.find(row => row.key === process.env.AGENT_USERNAME);
  if (!agent?.active || agent.status === 'Retiring') throw new Error('The agent is unavailable.');
  return agent;
}

export function registerTeamTools(server) {
  const result = value => ({content:[{type:'text',text:JSON.stringify(value).slice(0,48000)}]});
  if (process.env.TEAM_ROLE === 'scouter' && process.env.AGENT_USERNAME === 'scouter') {
    server.registerTool('search_publishers',{description:'Look up public skills, tools and MCP servers by public keywords.',
      inputSchema:{topic:z.string().max(100)}}, async ({topic}) => result(await research(topic)));
    server.registerTool('read_publisher_page',{description:'Read a public HTTPS publisher page as reference material.',
      inputSchema:{url:z.string().url()}}, async ({url}) => {
      const response = await publicFetch(url);
      if (!response.ok) throw new Error('Publisher unavailable.');
      const bytes = Buffer.from(await response.arrayBuffer());
      return result({sha256:createHash('sha256').update(bytes).digest('hex'), bytes:bytes.length,
        text:bytes.toString('utf8').slice(0,20000)});
    });
  }
  if (!process.env.MATRIX_CATALOG_PATH) return;
  server.registerTool('list_enabled_tools',{description:'List this agent’s approved, verified tools.',inputSchema:{}},
    async () => result((await targetCatalog()).grants ?? {}));
  server.registerTool('use_enabled_tool',{description:'Use a verified tool within its approved scope.',
    inputSchema:{id:z.string(),action:z.string(),arguments:z.record(z.string(),z.unknown()).optional()}}, async input => {
      const tool = (await targetCatalog()).grants?.[input.id];
      if (!tool) throw new Error('This agent has no such tool grant.');
      try { return result(await useTool(tool,input.action,input.arguments)); }
      catch { throw new Error('Tool unavailable. Check the approved scope, connection, and target credentials.'); }
    });
}

export async function checkTargetTools(agent, db, publish) {
  if (!agent || !Object.keys(agent.toolChecks ?? {}).length) return;
  db.exec('CREATE TABLE IF NOT EXISTS tool_receipts (change TEXT PRIMARY KEY, status TEXT, sent INTEGER DEFAULT 0)');
  for (const {change, tool} of Object.values(agent.toolChecks)) {
    if (!/^[a-f0-9]{64}$/u.test(change)) continue;
    let receipt = db.prepare('SELECT * FROM tool_receipts WHERE change=?').get(change);
    if (receipt?.sent) continue;
    if (!receipt) {
      let status = 'verified';
      try { await useTool(tool); }
      catch (error) { status = error.code === 'ENOENT' || /HTTP (401|403)|sign-in/i.test(error.message) ? 'credentials' : 'failed'; }
      db.prepare('INSERT OR IGNORE INTO tool_receipts (change,status) VALUES (?,?)').run(change,status);
      receipt = {status};
    }
    const session = 'tool-check-' + change;
    await publish({event:'office:tool_check',workspace:'agents-team',idempotency_key:session,
      data:{session_id:session,change,status:receipt.status,timestamp:new Date().toISOString()}});
    db.prepare('UPDATE tool_receipts SET sent=1 WHERE change=?').run(change);
  }
}
