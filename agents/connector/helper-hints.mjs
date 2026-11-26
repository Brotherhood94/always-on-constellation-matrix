import { randomInt } from 'node:crypto';

const hints = [
  ['office', 'Did you know that you can mention Task Manager in Office to coordinate a task? The result stays in the original thread.'],
  ['graph', 'Did you know that Team connections on the dashboard shows which agent can hand work to which teammate?'],
  ['dashboard', 'Did you know that the dashboard and Context Intelligence links are pinned in Agents → VPS Alerts?'],
  ['desktop', 'Did you know that Office workers can share files through their desktop folder?'],
  ['tailnet:worker', 'Did you know that you can install a remote worker on a Linux machine on your tailnet and connect it to the same Office?'],
  ['tailnet:move', 'Did you know that you can move an always-on agent to another enrolled Tailscale host with the agent move command on the VPS?'],
  ['dot:office', 'Did you know that Dot can open an Office task from ChatGPT using the Constellation Office connection?'],
  ['telegram:drafts', 'Did you know that Telegram Agent can draft a message for you to review in Telegram Bridge before it is sent?'],
];

export function hintPreference(text) {
  const command = text.normalize('NFKC').toLocaleLowerCase('en').trim()
    .replace(/[.!?]+$/u, '').replace(/^(?:please|per favore)\s+/u, '').replace(/[, ]+(?:please|per favore)$/u, '');
  if (/^(?:stop(?: sending)? (?:me )?(?:the )?(?:daily )?(?:hints|tips)|turn off (?:the )?(?:daily )?(?:hints|tips)|smetti (?:di )?(?:mandarmi|inviarmi|inviare) (?:i )?(?:suggerimenti|consigli)(?: giornalieri)?|disattiva (?:i )?(?:suggerimenti|consigli)(?: giornalieri)?)$/u.test(command)) return false;
  if (/^(?:resume(?: sending)? (?:me )?(?:the )?(?:daily )?(?:hints|tips)|turn on (?:the )?(?:daily )?(?:hints|tips)|riattiva (?:i )?(?:suggerimenti|consigli)(?: giornalieri)?|riprendi (?:a )?(?:mandarmi|inviarmi|inviare) (?:i )?(?:suggerimenti|consigli)(?: giornalieri)?)$/u.test(command)) return true;
  return undefined;
}

export function dailyHints(db, { timezone = 'UTC', features = [], choose = randomInt } = {}) {
  const calendar = new Intl.DateTimeFormat('en-CA', { timeZone: timezone, year: 'numeric',
    month: '2-digit', day: '2-digit', hour: '2-digit', hourCycle: 'h23' });
  const enabledFeatures = new Set(features);
  const eligible = new Map(hints.filter(([id]) => !id.includes(':') || enabledFeatures.has(id.split(':')[0])));
  db.exec(`
    CREATE TABLE IF NOT EXISTS helper_hints (
      day TEXT PRIMARY KEY, hint TEXT NOT NULL, cycle INTEGER NOT NULL, event TEXT
    );
  `);
  const read = db.prepare("SELECT value FROM settings WHERE name='dailyHints'");
  const preference = db.prepare("INSERT OR REPLACE INTO settings VALUES ('dailyHints', ?)");
  const get = db.prepare('SELECT * FROM helper_hints WHERE day=?');
  const recent = db.prepare('SELECT hint,cycle FROM helper_hints WHERE event IS NOT NULL ORDER BY day DESC');
  const reserve = db.prepare('INSERT OR IGNORE INTO helper_hints VALUES (?,?,?,NULL)');
  const complete = db.prepare('UPDATE helper_hints SET event=? WHERE day=? AND event IS NULL');
  const enabled = () => read.get()?.value !== 'off';
  return {
    setEnabled(value) { preference.run(value ? 'on' : 'off'); },
    async tick(now, send, canSend) {
      if (!enabled()) return;
      const { year, month, day, hour } = Object.fromEntries(calendar.formatToParts(now).map(({ type, value }) => [type, value]));
      if (Number(hour) < 10) return;
      const date = [year, month, day].join('-');
      const existing = get.get(date);
      if (existing?.event || !await canSend() || !enabled()) return;
      if (!existing) {
        const history = recent.all();
        let cycle = history[0]?.cycle ?? 0;
        const seen = new Set(history.filter(row => row.cycle === cycle).map(row => row.hint));
        let available = [...eligible.keys()].filter(id => !seen.has(id));
        if (!available.length) {
          cycle++;
          available = [...eligible.keys()].filter(id => id !== history[0]?.hint);
        }
        reserve.run(date, available[choose(available.length)], cycle);
      }
      const pending = get.get(date);
      const body = eligible.get(pending.hint);
      if (!body || !enabled()) return;
      complete.run(await send('helper-daily-hint-' + date, {
        msgtype: 'm.text',
        body: body + '\n\nSay “stop sending hints” to turn off daily hints.',
      }), date);
    },
  };
}
