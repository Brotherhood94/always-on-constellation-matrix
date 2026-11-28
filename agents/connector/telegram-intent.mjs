export function explicitTelegramRequest(text) {
  return /^(?:telegram\s*[:,-]|(?:ask|chiedi|delega)\s+(?:(?:to|a|al)\s+)?telegram\s+(?:agent|agente)\b)/iu.test(text) ||
    /^(?:scrivi|invia|manda|send|write|prepara|draft|aggiungi|add|apri|open|leggi|read|riassumi|summarize)\b[^\n]{0,300}\b(?:on|su|via)\s+telegram\b/iu.test(text);
}

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
  const body = content.body.replace(/^(?:>[^\n]*\n)+\n/u, '');
  return body.replace(prefix, '');
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

export function telegramSendPayload(input) {
  if (!input || (input.type !== undefined && !['direct', 'group'].includes(input.type)) ||
      typeof input.target !== 'string' || typeof input.text !== 'string' || !input.text.trim() ||
      Buffer.byteLength(input.text) > 3500) return null;
  const target = input.target.trim();
  if (!target || Buffer.byteLength(target) > 120 || /[\p{Cc}\p{Cf}]/u.test(target)) return null;
  return { target, ...(input.type && { type: input.type }), text: input.text };
}

export function telegramTaskPayload(input) {
  if (!input || typeof input !== 'object') return null;
  const action = input.action ?? 'send';
  if (['send', 'draft'].includes(action)) {
    const message = telegramSendPayload(input);
    return message && { ...(input.action && { action }), ...message };
  }
  if (['list', 'status'].includes(action)) return { action };
  if (!['open', 'unlink', 'lookup', 'read', 'create_group', 'add_members'].includes(action) ||
      typeof input.target !== 'string' ||
      (input.type !== undefined && !['direct', 'group'].includes(input.type))) return null;
  const title = compact(input.target).replace(/^[“"]|[”"]$/gu, '');
  const target = action === 'create_group'
    ? (title && Buffer.byteLength(title) <= 120 && !/[\p{Cc}\p{Cf}]/u.test(title) && { target: title, type: 'group' })
    : destination(input.target, input.type);
  if (!target) return null;
  if (action === 'read') {
    if (typeof input.question !== 'string' || !input.question.trim() || Buffer.byteLength(input.question) > 1000) return null;
    return { action, ...target, question: input.question };
  }
  if (action === 'create_group' || action === 'add_members') {
    if (target.type === 'direct' || !Array.isArray(input.members) || input.members.length < 1 || input.members.length > 20 ||
        input.members.some(member => typeof member !== 'string' || !destination(member, 'direct') ||
          destination(member, 'direct').type !== 'direct')) return null;
    return { action, target: target.target, type: 'group', members: input.members.map(member => destination(member, 'direct').target) };
  }
  return { action, ...target };
}

export function telegramIntent(body, { office = false } = {}) {
  if (typeof body !== 'string' || Buffer.byteLength(body) > 4800 || /^\s*[>`]/u.test(body)) return null;
  let input = body.trim();
  if (office && !/\btelegram\b/iu.test(input)) return null;
  if (/^(?:telegram[ :]+(?:bridge )?status|(?:check|show|verifica|mostra)\s+(?:the |lo )?(?:telegram bridge status|stato (?:del bridge )?telegram))[.!?]?$/iu.test(input)) {
    return { action: 'status' };
  }
  const legacyDraft = /^telegram\s+draft\s+(direct|group)\s+"([^"\r\n]+)"\s*:\s*([\s\S]+)$/iu.exec(input);
  if (legacyDraft) return telegramTaskPayload({ action: 'draft', type: legacyDraft[1].toLowerCase(),
    target: legacyDraft[2], text: legacyDraft[3] });
  const telegramPrefix = /^telegram(?:\s*[,\-:]\s*|\s+(?=(?:add|open|remove|unlink|send|list)\b))/iu;
  const explicitChannel = telegramPrefix.test(input);
  input = input.replace(telegramPrefix, '').replace(/^(?:please|per favore),?\s+/iu, '');
  const findAndDraft = /^(?:find|look up) (.{1,120}?) on telegram and (?:prepare|draft) a (private )?message (?:saying|that says) ([\s\S]+)$/iu.exec(input);
  if (findAndDraft) input = `prepare a ${findAndDraft[2] ?? ''}message to ${findAndDraft[1]} saying ${findAndDraft[3]}`;
  const plain = input.replace(/\s+(?:on|su) telegram[.!]?$/iu, '').replace(/[.!?]$/u, '');
  if (/^(?:list|show(?: me)?|elenca|mostrami)(?:\s+(?:my|the|le|mie))*\s+(?:(?:telegram\s+)?rooms|(?:stanze|chat)(?:\s+(?:di )?telegram)?)$/iu.test(plain)) {
    return { action: 'list' };
  }
  const create = /^(?:create (?:a )?(?:telegram )?group|crea (?:(?:un|il) )?gruppo(?: telegram)?) (?:named |called |chiamato )?(.+?) (?:with|con) (.+)$/iu.exec(plain);
  const add = /^(?:add|invite|aggiungi|invita) (.+?) (?:to (?:the )?group|al gruppo|nel gruppo) (.+)$/iu.exec(plain);
  if (create || add) {
    const members = (create?.[2] ?? add[1]).split(/\s*,\s*|\s+(?:and|e)\s+/iu);
    return telegramTaskPayload({ action: create ? 'create_group' : 'add_members',
      target: create?.[1] ?? add[2], members });
  }
  const quotedSend = /^(?:send|invia|manda|scrivi)\s+(?:"([^"\r\n]+)"|“([^”\r\n]+)”|«([^»\r\n]+)»)\s+(?:to|ad?)\s+(@[a-z0-9_]{4,32})(\s+(?:(?:on|su)\s+)?telegram)?[.!]?$/iu.exec(input);
  if (quotedSend) {
    if (office && !explicitChannel && !quotedSend[5]) return null;
    const text = quotedSend[1] ?? quotedSend[2] ?? quotedSend[3];
    return text.trim() && Buffer.byteLength(text) <= 3500
      ? { action: 'send', target: quotedSend[4], type: 'direct', text }
      : null;
  }
  const exact = /^(?:invia|manda|send)\s+(?:(?:una sola volta|once)\s+)?(?:il testo(?: esatto)?|(?:the )?exact text)\s+(?:"([^"\r\n]+)"|“([^”\r\n]+)”|«([^»\r\n]+)»)\s+(?:alla chat privata(?: già autorizzata e verificata)? di|to the private chat (?:of|with))\s+(.+)$/iu.exec(plain);
  if (exact) {
    const namedChannel = /\s+(?:on|su) telegram[.!]?$/iu.test(input);
    if (office && !explicitChannel && !namedChannel) return null;
    const dest = destination(exact[4], 'direct');
    const text = exact[1] ?? exact[2] ?? exact[3];
    return dest?.type === 'direct' && text.trim() && Buffer.byteLength(text) <= 3500 ? { action: 'send', ...dest, text } : null;
  }
  const draft = /^(?<verb>prepare|draft|send|write|prepara|scrivi|invia|manda)(?:\s+(?:a|an|un|una))?\s+(?:(?<private>private|privat[oa])\s+)?(?:(?:telegram\s+)?(?:message|messaggio|bozza|draft)(?:\s+(?<privateAfter>privat[oa]|private))?\s+)?(?:to|for|ad?|per|nel)\s+(?<target>[^\r\n]{1,130}?)(?:\s+(?:on|su) telegram)?(?:(?:\s+(?:saying|that says|dicendo|che dice|che)|\s*:\s+)\s*(?<text>[\s\S]+)|\s+(?:(?:con il testo(?: esatto)?|with (?:the )?(?:exact )?text)\s+)?(?<literal>["“«][\s\S]+))$/iu.exec(input);
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
      action: /^(?:send|write|invia|scrivi|manda)$/iu.test(verb) ? 'send' : 'draft', ...dest, text: message,
    } : null;
  }
  const readChat = /^(?:leggi|riassumi|read|summarize) (?:la |the )?(?:conversazione|chat|conversation) (?:con|di|with) (.+)$/iu.exec(plain);
  const summary = readChat ?? /^(?:summarize|summarise|read|what happened|give me an update|riassumi|leggi|aggiornami|cosa è successo)\b.*?\b(?:in|nel|nella|su|about)\s+(.+)$/iu.exec(plain);
  if (summary) {
    const chat = summary[1].replace(/^(?:(?:the |la )?(?:chat|conversazione) (?:with|con|di) )/iu, '')
      .replace(/\s+(?:today|yesterday|last 24 hours|last 7 days|oggi|ieri|nelle ultime 24 ore|negli ultimi 7 giorni)$/iu, '');
    const dest = destination(chat);
    return dest && { action: 'read', ...dest, question: input };
  }
  const chatRequest = compact(plain).replace(/^chiedi (?:a|al) (?:telegram|teelgram) (?:agent|agente|agetn) di /iu, '');
  const linkChat = /^(?:connettere|collegare|connetti|collega|apri) /iu;
  if (linkChat.test(chatRequest)) {
    const telegramSpace = /^(?:in|nel|nello|allo) (?:spazio|sapzio) telegram /iu;
    const chat = chatRequest.replace(linkChat, '').replace(telegramSpace, '');
    const username = /^(?:una|la) (?:nuova )?chat(?: privata)? con (?:l['’]utente (?:il cui username (?:è|e)|con username) )?(@[a-z0-9_]{4,32})$/iu.exec(chat);
    if (username) return { action: 'open', target: username[1], type: 'direct' };
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
