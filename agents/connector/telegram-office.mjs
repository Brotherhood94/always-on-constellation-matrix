import { request } from 'node:http';

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

export function requestTelegramOffice(socketPath, id, operation) {
  return requestTelegramService(socketPath, { id, ...operation });
}

export async function requestTelegramService(socketPath, input, timeout = 55_000) {
  return new Promise((resolve, reject) => {
    const req = request({ socketPath, method: 'POST', path: '/', signal: AbortSignal.timeout(timeout),
      headers: { 'content-type': 'application/json' } }, res => {
      let data = '';
      res.setEncoding('utf8');
      res.on('error', reject);
      res.on('data', chunk => {
        data += chunk;
        if (data.length > 8192) req.destroy(new Error('Invalid Telegram office response.'));
      });
      res.on('end', () => {
        try { resolve(JSON.parse(data)); } catch (error) { reject(error); }
      });
    });
    req.on('error', reject);
    req.end(JSON.stringify(input));
  });
}
