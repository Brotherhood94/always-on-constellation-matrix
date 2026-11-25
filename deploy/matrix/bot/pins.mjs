import { createHash } from 'node:crypto';

const labels = { dashboard: 'Open dashboard', context: 'Open Context Intelligence' };
const escapeHtml = value => value.replaceAll('&', '&amp;').replaceAll('"', '&quot;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');

function message(label, value) {
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
  return { msgtype: 'm.text', body: `${label} — ${url.href}`, format: 'org.matrix.custom.html',
    formatted_body: `<a href="${escapeHtml(url.href)}">${label}</a>` };
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
  const contents = Object.fromEntries(Object.entries(labels).map(([key, label]) => [key, message(label, links[key])]));
  for (const [key, content] of Object.entries(contents)) {
    const current = state.entries[key];
    if (current?.body === content.body && current.recipients === recipients) continue;
    const transaction = createHash('sha256')
      .update(JSON.stringify(['workspace-shortcut', key, current?.event ?? null, content.body, recipients])).digest('hex');
    const event = await send(transaction, content);
    if (current) state.replaced.push(current.event);
    state.entries[key] = { body: content.body, event, recipients };
    await save(state);
  }
  const pinned = await getPinned();
  const desired = [...new Set([
    ...pinned.filter(event => !state.replaced.includes(event)),
    ...Object.keys(labels).map(key => state.entries[key].event),
  ])];
  if (JSON.stringify(pinned) !== JSON.stringify(desired)) await setPinned(desired);
  if (state.replaced.length) {
    state.replaced = [];
    await save(state);
  }
  return true;
}
