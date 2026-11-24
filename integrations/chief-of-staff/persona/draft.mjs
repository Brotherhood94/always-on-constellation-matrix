import { writeFile } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { runTurn } from '../../../agents/connector/instance-acp.mjs';

const [role, id] = process.argv.slice(2);
const file = '/app/chief-of-staff/persona/brief.mjs';
for (const option of ['collect', 'prompt']) {
  const report = spawnSync(process.execPath, [file, option, role, id], { stdio: ['ignore', 'pipe', 'inherit'], encoding: 'utf8' });
  if (report.status !== 0) throw new Error('Could not prepare the persona brief');
  if (option === 'prompt') {
    const draft = await runTurn({
      url: process.env.INSTANCE_ACP_URL, workspace: '/workspace', revision: '0.22.17',
      token: '/acp/credential-token', ca: '/acp/ca.pem', turnTimeoutMs: 160000,
    }, null, report.stdout, () => {});
    await writeFile('/run-data/draft.json', draft, { mode: 0o600 });
  }
}
