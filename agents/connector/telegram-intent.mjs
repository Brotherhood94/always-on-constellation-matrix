const compact = value => value.trim().replace(/[ \t]+/gu, ' ');
const normalize = value => compact(value).normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase();
export const matchTelegramName = (candidate, target) => normalize(candidate) === normalize(target);

export function telegramMention(content, user, name) {
  const mentions = content?.['m.mentions'];
  if (content?.msgtype !== 'm.text' || content['m.new_content'] || mentions?.room ||
      !Array.isArray(mentions?.user_ids) || mentions.user_ids.length !== 1 || mentions.user_ids[0] !== user ||
      typeof content.body !== 'string') return null;
  const label = [name, user, user.split(':', 1)[0]].map(value => value.replace(/[.*+?^${}()|[\]\\]/gu, '\\$&'));
  const prefix = new RegExp(`^@?(?:${label.join('|')})[,:]?\\s+`, 'iu');
  return prefix.test(content.body) ? content.body.replace(prefix, '') : content.body;
}

function destination(value, type) {
  let target = compact(value).replace(/^\s*[“"]|[”"]\s*$/gu, '');
  const direct = /^(?:(?:the )?private chat with|(?:la )?chat privata con|(?:il )?privato)\s+/iu;
  if (direct.test(target)) {
    if (type === 'group') return null;
    type = 'direct';
    target = target.replace(direct, '');
  }
  const group = /^(?:the |il |un )?(?:telegram )?(?:group|gruppo)\s+|\s+(?:group|gruppo)$/giu;
  if (group.test(target)) {
    type = 'group';
    target = target.replace(group, '').replace(/^(?:the|il|un)\s+/iu, '').trim();
  }
  const describedId = /^(?:il seguente utente|l['’]utente) su telegram (?:che ha|con) id(?:-peer)?\s+([1-9][0-9]{0,18})$/iu.exec(target);
  if (describedId) target = describedId[1];
  target = target.replace(/^(?:(?:the )?(?:telegram )?user with|(?:l['’])?utente (?:con|che ha))\s+(?:id(?:-peer)?\s*)?|^(?:id(?:-peer)?(?:\s*:\s*|\s+))/iu, '');
  const groupId = /^(?:chat|channel):[1-9][0-9]{0,18}$/u;
  if (groupId.test(target) || /^https:\/\/t\.me\/c\/[1-9]\d{0,18}\/\d+\/?$/u.test(target)) type = 'group';
  else if (/^[1-9][0-9]{0,18}$/u.test(target)) {
    if (type === 'group') return null;
    target = 'user:' + target;
  }
  if (/^@|^user:/u.test(target)) {
    if (type === 'group') return null;
    type = 'direct';
  }
  if (!target || Buffer.byteLength(target) > 120 || /[\p{Cc}\p{Cf};!?]|\b(?:and|then|or|oppure|poi)\b/iu.test(target)) return null;
  return { target, ...(type && { type }) };
}

export function telegramIntent(body, { office = false } = {}) {
  if (typeof body !== 'string' || Buffer.byteLength(body) > 4800 || /^\s*[>`]/u.test(body)) return null;
  let input = body.trim();
  if (office && !/\btelegram\b/iu.test(input)) return null;
  const telegramPrefix = /^telegram\s*[,\-:]\s*/iu;
  const explicitChannel = telegramPrefix.test(input);
  input = input.replace(telegramPrefix, '').replace(/^(?:please|per favore),?\s+/iu, '');
  const findAndDraft = /^(?:find|look up) (.{1,120}?) on telegram and (?:prepare|draft) a (private )?message (?:saying|that says) ([\s\S]+)$/iu.exec(input);
  if (findAndDraft) input = `prepare a ${findAndDraft[2] ?? ''}message to ${findAndDraft[1]} saying ${findAndDraft[3]}`;
  const plain = input.replace(/\s+(?:on|su) telegram[.!]?$/iu, '').replace(/[.!?]$/u, '');
  if (/^(?:list|show(?: me)?|elenca|mostrami)(?:\s+(?:my|the|le|mie))*\s+(?:(?:telegram\s+)?rooms|(?:stanze|chat)(?:\s+(?:di )?telegram)?)$/iu.test(plain)) {
    return { action: 'list' };
  }
  const exact = /^(?:invia|manda|send)\s+(?:(?:una sola volta|once)\s+)?(?:il testo(?: esatto)?|(?:the )?exact text)\s+(?:"([^"\r\n]+)"|“([^”\r\n]+)”|«([^»\r\n]+)»)\s+(?:alla chat privata(?: già autorizzata e verificata)? di|to the private chat (?:of|with))\s+(.+)$/iu.exec(plain);
  if (exact) {
    const namedChannel = /\s+(?:on|su) telegram[.!]?$/iu.test(input);
    if (office && !explicitChannel && !namedChannel) return null;
    const dest = destination(exact[4], 'direct');
    const text = exact[1] ?? exact[2] ?? exact[3];
    return dest?.type === 'direct' && text.trim() && Buffer.byteLength(text) <= 3500 ? { action: 'send', ...dest, text } : null;
  }
  const draft = /^(?<verb>prepare|draft|send|prepara|scrivi|invia|manda)(?:\s+(?:a|an|un|una))?\s+(?:(?<private>private|privat[oa])\s+)?(?:(?:telegram\s+)?(?:message|messaggio|bozza|draft)(?:\s+(?<privateAfter>privat[oa]|private))?\s+)?(?:to|for|ad?|per|nel)\s+(?<target>[^\r\n]{1,130}?)(?:\s+(?:on|su) telegram)?(?:(?:\s+(?:saying|that says|dicendo|che dice|che)|\s*:\s+)\s*(?<text>[\s\S]+)|\s+(?<literal>["“«][\s\S]+))$/iu.exec(input);
  if (draft) {
    const { verb, target, text, literal } = draft.groups;
    if (office && !explicitChannel && !/\btelegram\b/iu.test(input.slice(0, -(text ?? literal).length))) return null;
    const dest = destination(target, draft.groups.private || draft.groups.privateAfter ? 'direct' : undefined);
    const value = (text ?? literal).trim();
    const quoted = literal ? /^(?:"([^"]+)"|“([^”]+)”|«([^»]+)»)\.?$/u.exec(value)
      : /^(?:"([\s\S]+)"|“([\s\S]+)”|«([\s\S]+)»)\.?$/u.exec(value);
    if (literal && !quoted) return null;
    const message = quoted ? quoted[1] ?? quoted[2] ?? quoted[3] : value;
    return dest && message && Buffer.byteLength(message) <= 3500 ? {
      action: /^(?:send|invia|scrivi|manda)$/iu.test(verb) ? 'send' : 'draft', ...dest, text: message,
    } : null;
  }
  const summary = /^(?:summarize|summarise|what happened|give me an update|riassumi|aggiornami|cosa è successo)\b.*?\b(?:in|nel|nella|su|about)\s+(.+)$/iu.exec(plain);
  if (summary) {
    const dest = destination(summary[1]);
    return dest && { action: 'read', ...dest, question: input };
  }
  const room = /^(add|open|unlink|remove|find|search for|aggiungi|apri|rimuovi|scollega|cerca|trova)\s+(.+)$/iu.exec(plain);
  if (!room) return null;
  const verb = room[1].toLowerCase();
  let target = room[2].replace(/^(?:the |a |la |una )?(?:(?:private |privata )?(?:chat|room|stanza)(?:\s+privata)?\s+(?:with|con|for|di)\s+|il contatto )/iu, '');
  const type = /\b(?:private chat|chat privata)\b/iu.test(room[2]) ? 'direct' : undefined;
  target = target.replace(/\s+(?:to|from|in)\s+(?:my |the )?(?:telegram(?: (?:rooms|space))?|rooms)$|\s+(?:alle|dalle|nelle)\s+(?:mie )?(?:stanze|room)(?:\s+(?:di |del )?telegram)?$|\s+(?:nel|dallo|allo) spazio telegram$/iu, '');
  const dest = destination(target, type);
  if (!dest) return null;
  // Keep invitation, sharing, and compound requests out of the room-mutation path.
  if (/\b(?:invite|share|invita|condividi|manda|invia|send|tutte|all (?:the )?rooms)\b/iu.test(target) ||
      /\s+(?:e|to|from|into|alle|dalle)\s+/iu.test(target)) return null;
  const action = /^(?:add|open|aggiungi|apri)$/u.test(verb) ? 'open' :
    /^(?:unlink|remove|rimuovi|scollega)$/u.test(verb) ? 'unlink' : 'lookup';
  return { action, ...dest };
}
