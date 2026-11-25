import { createHash } from 'node:crypto';

const labels = { dashboard: 'Open dashboard', context: 'Open Context Intelligence' };
const escapeHtml = value => value.replaceAll('&', '&amp;').replaceAll('"', '&quot;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');

function shortcut(label, value) {
  if (typeof value !== 'string') throw new TypeError('Expected an HTTPS link');
  const url = new URL(value);
  if (url.protocol !== 'https:' || url.username || url.password || url.hash) {
    throw new TypeError('Use HTTPS shortcut URLs without credentials, parameters, or fragments');
  }
  if (url.search) {
    const values = [...url.searchParams];
    if (values.length !== 1 || values[0][0] !== 'connectURL') throw new TypeError('Unexpected URL parameter');
    const graph = new URL(values[0][1]);
    if (graph.protocol !== 'bolt+s:' || graph.hostname !== url.hostname || graph.username ||
        graph.password || graph.search || graph.hash || graph.pathname || !graph.port) {
      throw new TypeError('Unexpected Neo4j connection URL');
    }
  }
  return { body: `${label} — ${url.href}`, html: `<a href="${escapeHtml(url.href)}">${label}</a>` };
}

export function shortcutRecipientKey(owner, devices) {
  const recipients = [];
  for (const id of Object.keys(devices ?? {}).sort()) {
    const device = devices[id];
    const curve = device?.keys?.[`curve25519:${id}`];
    const signing = device?.keys?.[`ed25519:${id}`];
    if (device?.user_id === owner && device.device_id === id &&
        typeof curve === 'string' && curve && typeof signing === 'string' && signing &&
        Array.isArray(device.algorithms) && device.algorithms.includes('m.megolm.v1.aes-sha2')) {
      recipients.push([id, curve, signing]);
    }
  }
  return recipients.length ? createHash('sha256').update(JSON.stringify(recipients)).digest('hex') : null;
}

export async function reconcilePins({ recipientKey, getPinned, send, setPinned, save }, links, state) {
  const recipients = await recipientKey();
  if (!recipients) return false;
  const shortcuts = Object.entries(labels).map(([key, label]) => [key, shortcut(label, links[key])]);
  const previous = [...new Set(shortcuts.map(([key]) => state.entries[key]?.event).filter(Boolean))];
  let [event] = previous;
  if (previous.length !== 1 || shortcuts.some(([key, { body }]) =>
    state.entries[key]?.body !== body || state.entries[key].recipients !== recipients)) {
    const content = {
      msgtype: 'm.text', format: 'org.matrix.custom.html',
      body: ['Workspace shortcuts', ...shortcuts.map(([, { body }]) => body)].join('\n'),
      formatted_body: ['<strong>Workspace shortcuts</strong>', ...shortcuts.map(([, { html }]) => html)].join('<br>'),
    };
    const transaction = createHash('sha256')
      .update(JSON.stringify(['workspace-shortcuts', previous, content.body, recipients])).digest('hex');
    event = await send(transaction, content);
    state.replaced.push(...previous);
    // Keep one record per link so the setup guide can verify both URLs against the deployment settings.
    for (const [key, { body }] of shortcuts) state.entries[key] = { body, event, recipients };
    await save(state);
  }
  const pinned = await getPinned();
  const desired = [...new Set([...pinned.filter(id => !state.replaced.includes(id)), event])];
  if (JSON.stringify(pinned) !== JSON.stringify(desired)) await setPinned(desired);
  if (state.replaced.length) {
    state.replaced = [];
    await save(state);
  }
  return true;
}
