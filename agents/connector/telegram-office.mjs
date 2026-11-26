import { request } from 'node:http';
import { readFile } from 'node:fs/promises';

const escape = value => value.replace(/[.*+?^${}()|[\]\\]/gu, '\\$&');

const contactWording = [
  [
    /^(?:aggiungi (?:il contatto )?|apri (?:la|una) chat con )(@[a-z0-9_]{4,32}) (.+)$/iu,
    /^(?:alle (?:room|stanze) (?:di|del|dello|nel|nello)|su|in|nel|nello|allo) (?:(?:spazio|space) )?(?:telegram|teelgram)(?: space)?$/iu,
  ],
  [
    /^(?:please )?(?:add (?:the contact )?|open (?:a|the) chat with )(@[a-z0-9_]{4,32}) (.+)$/iu,
    /^(?:to|in|on) (?:the )?telegram(?: (?:rooms(?: list)?|space))?$/iu,
  ],
];

export function telegramOfficeCommand(body, name, id) {
  const mention = new RegExp(`^(?:${escape(id)}|@?${escape(name)})[,:]?\\s+`, 'iu');
  const input = body.replace(mention, '').trimStart();
  if (!/^telegram\s+(?:add|remove|draft)\b/iu.test(input)) {
    const sentence = input.trimEnd().replace(/[.!]$/u, '').replace(/[ \t]+/gu, ' ');
    for (const [command, destination] of contactWording) {
      const match = command.exec(sentence);
      if (match && destination.test(match[2])) return { action: 'add', target: match[1] };
    }
    return null;
  }
  const draft = /^telegram\s+draft\s+(direct|group)\s+"([^"\n]+)"[ \t]*:[ \t]?([\s\S]+)$/iu.exec(input);
  if (draft) return { action: 'draft', type: draft[1].toLowerCase(), target: draft[2], text: draft[3] };
  const add = /^telegram\s+add\s+(\S+)$/iu.exec(input.trimEnd());
  if (add) return { action: 'add', target: add[1] };
  const remove = /^telegram\s+remove\s+(.+)$/iu.exec(input.trimEnd());
  if (remove) return { action: 'remove', target: remove[1].replace(/^"([^"\n]+)"$/u, '$1') };
  return { action: 'help' };
}

export const telegramOfficeHelp = 'Use `telegram add @username`, `telegram add user:1001`, `telegram add channel:1234567890`, ' +
  '`telegram remove "Name"`, or `telegram draft direct "Name": your message`. Use `group` for a group draft.';

export const telegramTaskStatus = {
  pending: 'Telegram Agent accepted the request.',
  needs_input: 'Telegram Agent needs your input in the private thread.',
  awaiting_approval: 'The Telegram draft is ready for your approval.',
  sending: 'Telegram Agent is waiting for a delivery receipt.',
  completed: 'Telegram Agent completed the request.',
  blocked: 'Telegram Agent could not complete the request. Specify one Telegram chat and one action. Check the private link for details when available.',
  declined: 'The operator declined the Telegram request. No message was sent.',
  unconfirmed: 'Telegram delivery is unconfirmed. Check the private receipt before retrying.',
};

export const telegramTerminal = status => ['completed', 'blocked', 'declined'].includes(status);

export const telegramHandoffKey = 'org.matrixconstellation.telegram.handoff';
export const telegramReceiptKey = 'org.matrixconstellation.telegram.receipt';
const privateTelegramLink = /^https:\/\/matrix\.to\/#\/(?:%21|!)[A-Za-z0-9_.:%-]+\/(?:%24|\$)[A-Za-z0-9_:%.-]+$/iu;

export function telegramReceipt(result) {
  if (!result || !Object.hasOwn(telegramTaskStatus, result.status)) return null;
  if (result.office === true) return { status: result.status, office: true,
    ...(result.status === 'unconfirmed' && result.terminal === true && { terminal: true }) };
  if (typeof result.link === 'string' && privateTelegramLink.test(result.link)) return { status: result.status, link: result.link };
  return result.status === 'blocked' && result.link === undefined ? { status: 'blocked' } : null;
}

export function requestTelegramOffice(socketPath, id, operation) {
  return requestTelegramService(socketPath, { id, ...operation });
}

export async function relocationCall(operation, payload, timeout = 55_000) {
  const { AGENT_MOBILITY_ENDPOINT: endpoint, AGENT_MOBILITY_KEY: agent,
    AGENT_MOBILITY_GENERATION: generation } = process.env;
  if (!endpoint || !agent || !generation) throw new Error('The private role connection is not configured.');
  const token = (await readFile('/mobility/rpc-token', 'utf8')).trim();
  const response = await fetch(endpoint + '/rpc', {
    method: 'POST', redirect: 'error', signal: AbortSignal.timeout(timeout),
    headers: { authorization: 'Bearer ' + token, 'content-type': 'application/json' },
    body: JSON.stringify({ agent, generation, operation, payload }),
  });
  if (!response.ok) throw new Error('The coordinator refused the role call.');
  return response.json();
}

export async function requestTelegramService(socketPath, input, timeout = 55_000, maxResponse = 8192) {
  if (socketPath.startsWith('role:')) return relocationCall(socketPath.slice(5), input, timeout);
  return new Promise((resolve, reject) => {
    const req = request({ socketPath, method: 'POST', path: '/', signal: AbortSignal.timeout(timeout),
      headers: { 'content-type': 'application/json' } }, res => {
      let data = '';
      res.setEncoding('utf8');
      res.on('error', reject);
      res.on('data', chunk => {
        data += chunk;
        if (Buffer.byteLength(data) > maxResponse) req.destroy(new Error('Invalid Telegram office response.'));
      });
      res.on('end', () => {
        try { resolve(JSON.parse(data)); } catch (error) { reject(error); }
      });
    });
    req.on('error', reject);
    req.end(JSON.stringify(input));
  });
}
