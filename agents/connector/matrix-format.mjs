import MarkdownIt from 'markdown-it';

export const replyFormatting = 'Lead with the requested result, decision, or value. For a single value, use one short line with a bold label and inline code for an exact identifier. ' +
  'Use bullets for distinct items, numbered lists for ordered steps, and brief bold headings when there are multiple sections. ' +
  'Use inline code for exact identifiers, paths and short commands, fenced code for multiline examples, descriptive link text, and italics for secondary notes. ' +
  'For a terminal outcome, put the result and verification, or the blocker and next step, in TASK_OUTCOME. Put only additional facts that do not repeat those fields in the visible reply text. ' +
  'An outcome without additional details can consist of the control line alone. Give the answer without a progress preamble. Keep control lines at the end, outside code blocks.';

const markdown = new MarkdownIt('commonmark', { html: false, breaks: true, linkify: false, typographer: false });
markdown.disable('image');
markdown.validateLink = href => /^(?:https?:\/\/|mailto:|matrix:)/i.test(href) && MarkdownIt.prototype.validateLink(href);

function escapeHtml(text) {
  const entities = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  return text.replace(/[&<>"']/g, character => entities[character]);
}

function withMentions(html, links) {
  if (!links) return html;
  if (html.startsWith('<p>')) return `<p>${links} ${html.slice(3)}`;
  return `<p>${links}</p>\n${html}`;
}

function comparable(text) {
  return text.trim().replace(/[.!]$/, '').replace(/`([^`\n]+)`|\*\*([^*\n]+)\*\*/g, (_, code, bold) => code ?? bold)
    .replace(/[ \t]+/g, ' ');
}

export function outcomeReplyText(body, outcome, issues = []) {
  const notes = (issues ?? []).filter(issue => issue !== outcome?.blocker);
  if (!outcome) {
    const plain = [body || 'The agent completed the turn without a text reply.', notes.join('\n')].filter(Boolean).join('\n\n');
    return { plain, rich: plain };
  }
  const completed = outcome.status === 'completed';
  const heading = completed ? 'Task completed' : 'Task blocked';
  const rows = completed
    ? [['Result', outcome.result], ['Verified', outcome.verification]]
    : [['Blocker', outcome.blocker], ['Can unblock', outcome.unblocker], ['Next step', outcome.action]];
  const bodyText = comparable(body);
  const extra = rows.some(([, text]) => comparable(text) === bodyText) ? '' : body;
  const afterSummary = [extra, notes.join('\n')].filter(Boolean);
  const plainSummary = [heading, ...rows.map(([label, text]) => `${label}: ${text}`)].join('\n');
  const richSummary = [`**${heading}**`, ...rows.map(([label, text]) => `**${label}:** ${text}`)].join('\n');
  return {
    plain: [plainSummary, ...afterSummary].join('\n\n'),
    rich: [richSummary, ...afterSummary].join('\n\n'),
  };
}

export function mentionMessage(body, target, richBody = body) {
  const targets = Array.isArray(target) ? target : target ? [target] : [];
  const mentioned = [...new Map(targets.map(person => [person.id, person])).values()];
  const content = {
    msgtype: 'm.text',
    body: mentioned.length ? `${mentioned.map(person => person.name).join(' ')} ${body}` : body,
    'm.mentions': mentioned.length ? { user_ids: mentioned.map(person => person.id) } : {},
  };
  const links = mentioned.map(person =>
    `<a href="https://matrix.to/#/${escapeHtml(person.id)}">${escapeHtml(person.name)}</a>`).join(' ');
  const richText = {
    ...content,
    format: 'org.matrix.custom.html',
    formatted_body: withMentions(markdown.render(richBody).trimEnd() || escapeHtml(body), links),
  };
  // Leave room for Matrix event metadata and the encrypted form of the payload.
  if (Buffer.byteLength(JSON.stringify(richText)) <= 40 * 1024) return richText;
  if (!links) return content;
  return { ...richText, formatted_body: `${links} ${escapeHtml(body).replaceAll('\n', '<br>')}` };
}
