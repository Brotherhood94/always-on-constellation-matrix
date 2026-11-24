export const telegramRequest = 'org.matrixconstellation.telegram.request';

const eventId = /^\$[^\s\x00-\x1f]{1,254}$/u;
const questionLimit = 1000;
const minute = 60_000;
const day = 24 * 60 * minute;
const localDate = new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Rome', dateStyle: 'short' });

function boundedText(value, limit) {
  return typeof value === 'string' && !!value.trim() && Buffer.byteLength(value) <= limit;
}

export function addressedRequest(event, settings) {
  const { content, sender } = event;
  const mentions = content?.['m.mentions'];
  if (!settings.dot || !eventId.test(event.event_id ?? '') || event.type !== 'm.room.message' ||
      content?.msgtype !== 'm.text' || content['m.new_content'] ||
      mentions?.room || !Array.isArray(mentions?.user_ids) || mentions.user_ids.length !== 1 ||
      mentions.user_ids[0] !== settings.agent) return null;

  const relation = content['m.relates_to'];
  if (sender === settings.owner && !content[telegramRequest] && typeof content.body === 'string') {
    if (relation && (relation.rel_type !== 'm.thread' || !eventId.test(relation.event_id) || relation.event_id === event.event_id)) return null;
    const question = content.body.replace(/^(?:>[^\n]*\n)+\n/u, '')
      .replace(/^\s*@?telegram[ -]agent(?::[a-z0-9.-]+)?[,:]?\s*/iu, '').trim();
    if (!boundedText(question, questionLimit)) return null;
    const input = normalized(question);
    const reply = /^(?:rispondi|reply|invia la risposta|send the reply)\b/u.test(input) ||
      /\b(?:proponi|prepara|scrivi|suggerisci|draft|suggest|prepare)\b.*\b(?:risposta|reply)\b/u.test(input);
    return { action: reply ? 'reply' : 'update', question, ...(relation && { thread: relation.event_id }) };
  }
  const request = content[telegramRequest];
  if (sender !== settings.dot || relation || !request || !boundedText(request.destination, 120) ||
      !['direct', 'group'].includes(request.chat_type)) return null;

  const target = { destination: request.destination.trim(), chatType: request.chat_type };
  if (request.action === 'update' && boundedText(request.question, questionLimit)) {
    return { ...target, action: 'update', question: request.question.trim() };
  }
  if (request.action === 'compose' && boundedText(request.text, 3500) &&
      (request.source_event === undefined || eventId.test(request.source_event))) {
    return { ...target, action: 'compose', text: request.text, source: request.source_event };
  }
  return null;
}

function normalized(value) {
  return value.normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
}

export function isDirect(binding) {
  return JSON.parse(binding.target).peer.startsWith('user:');
}

export function resolveDestination(request, destinations) {
  const explicit = request.destination !== undefined;
  const input = normalized(explicit ? request.destination : request.question);
  const words = ' ' + input + ' ';
  const kind = explicit ? request.chatType
    : /\b(privat[oa]|dirett[oa]|dm|direct|private)\b/u.test(input) ? 'direct'
      : /\b(gruppo|group)\b/u.test(input) ? 'group' : undefined;
  const matches = destinations.filter(({ name, binding }) => {
    const direct = isDirect(binding);
    if (!explicit && !kind && direct) return false;
    const aliases = [normalized(binding.id), normalized(name)].filter(Boolean);
    if (direct) {
      const first = normalized(name).split(' ')[0];
      if (first.length >= 3) aliases.push(first);
    }
    if (kind && (kind === 'direct') !== direct) return false;
    return aliases.some(alias => explicit ? input === alias : words.includes(' ' + alias + ' '));
  });
  if (matches.length !== 1) return null;
  return matches[0];
}

export function historyRange(question, now) {
  const plain = normalized(question);
  if (/\b(oggi|today)\b/u.test(plain)) {
    const date = localDate.format(now);
    return { since: now - 26 * 60 * minute, label: 'Today (Europe/Rome)', includes: ts => localDate.format(ts) === date };
  }
  const days = /\b(ultimi 7 giorni|last 7 days|ultima settimana|past week)\b/u.test(plain) ? 7 : 1;
  return { since: now - days * day, label: days === 7 ? 'Last 7 days' : 'Last 24 hours', includes: () => true };
}

export function matrixLink(room, event) {
  return 'https://matrix.to/#/' + encodeURIComponent(room) + '/' + encodeURIComponent(event);
}

function html(value) {
  return value.replace(/[&<>"']/gu, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
}

export function controlThread(root, reply = root) {
  return { rel_type: 'm.thread', event_id: root, is_falling_back: true, 'm.in_reply_to': { event_id: reply } };
}

export function updateReply(settings, requestEvent, body, sources = [], thread = requestEvent) {
  const users = [{ id: settings.owner, name: settings.owner.split(':')[0].slice(1) }, { id: settings.dot, name: 'Dot' }];
  return {
    msgtype: 'm.text',
    body: users.map(({ id }) => id).join(' ') + '\n' + body + sources.map(({ label, url }) => '\n' + label + ' ' + url).join(''),
    format: 'org.matrix.custom.html',
    formatted_body: users.map(({ id, name }) => '<a href="https://matrix.to/#/' + encodeURIComponent(id) +
      '">' + html(name) + '</a>').join(' ') + '<br>' + html(body).replaceAll('\n', '<br>') +
      sources.map(({ label, url }) => '<br><a href="' + html(url) + '">' + html(label) + '</a>').join(''),
    'm.mentions': { user_ids: users.map(({ id }) => id) },
    'm.relates_to': controlThread(thread, requestEvent),
  };
}
