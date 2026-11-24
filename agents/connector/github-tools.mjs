import { execFile } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { z } from 'zod';

const configPath = '/github-credentials/connection.json';
const tokenPattern = /^ghp_[a-zA-Z0-9]+$/;
const forbiddenDelete = /^(repos\/[^/]+\/[^/]+|(?:orgs|organizations|enterprises|users)\/[^/]+|user)$/i;
const forbiddenMutation = /\bdelete(?:Repository|Organization|Enterprise|User)\b/i;
const responseLimit = 65536;

function apiPath(path) {
  if (!path.startsWith('/') || path.includes('?') || path.includes('#')) {
    throw new Error('Use a GitHub API path starting with / and pass query parameters separately.');
  }
  const parts = path.slice(1).replace(/\/$/, '').split('/').map(decodeURIComponent);
  if (parts.some(part => !part || part === '.' || part === '..' || /[\p{Cc}/\\%]/u.test(part))) {
    throw new Error('Invalid GitHub API path.');
  }
  return { route: parts.map(encodeURIComponent).join('/'), decoded: parts.join('/') };
}

function decodeResponse(text) {
  if (!text.trim()) return { status: 'ok' };
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

async function connect() {
  const source = await readFile(configPath, 'utf8').catch(error => {
    if (error.code !== 'ENOENT') throw error;
  });
  if (!source) return;
  const { token } = JSON.parse(source);
  if (typeof token !== 'string' || !tokenPattern.test(token)) {
    throw new Error('Coder requires a GitHub personal access token (classic).');
  }
  const env = {
    PATH: '/usr/bin:/bin', HOME: '/tmp', GH_CONFIG_DIR: '/tmp/coder-gh', GH_HOST: 'github.com', GH_TOKEN: token,
    GH_PROMPT_DISABLED: '1', GH_NO_UPDATE_NOTIFIER: '1', GH_TELEMETRY: '0', NO_COLOR: '1',
  };

  return (method, route, body, includeHeaders = false) => {
    const args = ['api', '--hostname', 'github.com', '--method', method];
    if (body !== undefined) args.push('--input', '-');
    if (includeHeaders) args.push('--include');
    args.push('--', route);
    const payload = body === undefined ? undefined : JSON.stringify(body);
    if (payload && Buffer.byteLength(payload) > 1024 * 1024) throw new Error('GitHub request body exceeds 1 MiB.');
    return new Promise((resolve, reject) => {
      const child = execFile('/usr/bin/gh', args, { cwd: '/tmp', env, timeout: 20000, maxBuffer: 4 * 1024 * 1024 },
        (error, stdout, stderr) => {
          if (!error) return resolve(stdout);
          const status = stderr.match(/\(HTTP ([45]\d\d)\)/)?.[1];
          reject(new Error(status ? `GitHub returned HTTP ${status}. Check the request and GitHub permissions.` :
            'GitHub CLI request failed. Check connectivity or narrow the response.'));
        });
      child.stdin.on('error', () => {});
      child.stdin.end(payload);
    });
  };
}

function result(value, isError = false) {
  const text = JSON.stringify(value);
  return { isError, content: [{ type: 'text', text: text.length <= responseLimit ? text :
    `${text.slice(0, responseLimit)}\n[GitHub response truncated. Request a smaller page or a more specific API endpoint.]` }] };
}

export async function registerGitHubTools(server) {
  const request = await connect();
  if (!request) return;

  server.registerTool('github_api', {
    description: 'Call the GitHub REST API through gh for repositories and resources accessible to the connected account. Use write methods only when the user requested the change. Repository deletion is blocked.',
    inputSchema: {
      method: z.enum(['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE']),
      path: z.string().min(2).max(2048).describe('GitHub API path such as /user/repos or /repos/OWNER/REPO/pulls.'),
      query: z.record(z.string().max(100), z.union([z.string().max(2048), z.number(), z.boolean()])).optional(),
      body: z.json().optional(),
    },
    annotations: { openWorldHint: true },
  }, async ({ method, path, query = {}, body }) => {
    try {
      const { route, decoded } = apiPath(path);
      if (method === 'DELETE' && forbiddenDelete.test(decoded)) {
        throw new Error('Deleting a repository or an account that owns repositories is disabled.');
      }
      if (decoded.toLowerCase() === 'graphql') throw new Error('Use github_graphql for GraphQL operations.');
      const parameters = new URLSearchParams(Object.entries(query).map(([key, value]) => [key, String(value)])).toString();
      if (parameters.length > 4096) throw new Error('GitHub query parameters are too long.');
      return result(decodeResponse(await request(method, parameters ? `${route}?${parameters}` : route, body)));
    } catch (error) {
      return result({ error: error.message }, true);
    }
  });

  server.registerTool('github_graphql', {
    description: 'Call GitHub GraphQL through gh with a query or an explicitly requested mutation. Repository deletion is blocked.',
    inputSchema: {
      query: z.string().trim().min(1).max(32768),
      variables: z.record(z.string(), z.json()).optional(),
    },
    annotations: { openWorldHint: true },
  }, async ({ query, variables }) => {
    try {
      if (forbiddenMutation.test(query)) throw new Error('Repository and account deletion mutations are disabled.');
      const response = decodeResponse(await request('POST', 'graphql', { query, variables }));
      return result(response, Boolean(response?.errors?.length && !response.data));
    } catch (error) {
      return result({ error: error.message }, true);
    }
  });
}

if (process.argv[2] === '--check') {
  try {
    const request = await connect();
    if (!request) throw new Error('GitHub credentials are missing.');
    const response = await request('GET', 'user', undefined, true);
    const [headers, ...body] = response.split(/\r?\n\r?\n/);
    const scopes = new Set(headers.match(/^x-oauth-scopes:[ \t]*([^\r\n]*)/im)?.[1].split(',').map(scope => scope.trim()));
    if (scopes.has('delete_repo')) throw new Error('Remove delete_repo from the token before connecting Coder.');
    if (!scopes.has('repo') || !scopes.has('workflow')) throw new Error('Create a classic token with repo and workflow scopes.');
    const account = JSON.parse(body.join('\n\n')).login;
    if (typeof account !== 'string' || !account) throw new Error('GitHub account identity could not be verified.');
    console.log(`GitHub account and token scopes verified: ${account}. Repository deletion scope is absent.`);
  } catch (error) {
    console.error(error instanceof SyntaxError ? 'GitHub connection verification failed.' : error.message);
    process.exitCode = 1;
  }
}
