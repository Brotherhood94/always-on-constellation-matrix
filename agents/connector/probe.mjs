import { runTurn } from './instance-acp.mjs';

let session;
const engine = {
  url: process.env.INSTANCE_ACP_URL, workspace: '/workspace', revision: '0.22.17',
  token: '/acp/credential-token', ca: '/acp/ca.pem',
};
const first = await runTurn(engine, null, 'Installation check. Reply with exactly CONSTELLATION_READY.', id => { session = id; });
if (first !== 'CONSTELLATION_READY' || !session) throw new Error('Amplifier sign-in probe failed');
const second = await runTurn(engine, session, 'Reply again with exactly CONSTELLATION_READY.', () => {});
if (second !== 'CONSTELLATION_READY') throw new Error('Amplifier session restore probe failed');
console.log('Amplifier provider and saved sessions are ready.');
