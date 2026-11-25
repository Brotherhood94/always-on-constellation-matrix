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
  return { label, url: url.href };
}

function message(title, sections) {
  const plain = line => typeof line === 'string' ? line : line.code ?? `${line.label} — ${line.url}`;
  const rich = line => {
    if (typeof line === 'string') return `<p>${escapeHtml(line)}</p>`;
    if (line.code !== undefined) return `<pre><code>${escapeHtml(line.code)}</code></pre>`;
    return `<p><a href="${escapeHtml(line.url)}">${escapeHtml(line.label)}</a></p>`;
  };
  return {
    msgtype: 'm.text', format: 'org.matrix.custom.html',
    body: [title, ...sections.map(([heading, lines]) => [heading, ...lines.map(plain)].join('\n'))].join('\n\n'),
    formatted_body: `<h3>${escapeHtml(title)}</h3>` + sections.map(([heading, lines]) =>
      `<h4>${escapeHtml(heading)}</h4>${lines.map(rich).join('')}`).join(''),
  };
}

function dotGuide({ command, systemd }) {
  return message('Connect Dot', [
    ['1. Browser · Create the tunnel', [
      'Create a Secure MCP Tunnel with Tunnels Read + Manage. Associate it with the intended ChatGPT workspace and copy its tunnel ID.',
      { label: 'Open OpenAI Platform Tunnels', url: 'https://platform.openai.com/settings/organization/tunnels' },
    ]],
    ['2. Browser · Create the runtime key', [
      'Create a Restricted API key with Tunnels Read + Use.',
      { label: 'Open OpenAI API Keys', url: 'https://platform.openai.com/settings/organization/api-keys' },
    ]],
    ['3. VPS terminal · Connect Dot', systemd ? [
      'Replace the placeholder with your tunnel ID and enter the runtime key at the hidden prompt.',
      { code: `${command} dot-connect tunnel_REPLACE_WITH_YOUR_ID` },
    ] : ['Enable systemd for this deployment before connecting the tunnel.']],
    ['4. Browser · Add Constellation Office', [
      'Add a private custom MCP server named Constellation Office. Choose Tunnel, select the same tunnel ID, and choose No authentication.',
      { label: 'Open ChatGPT Plugins', url: 'https://chatgpt.com/plugins' },
      'Ask Dot to run list_agents. An empty list is expected until you have activated an Office agent.',
    ]],
  ]);
}

function telegramGuide(command) {
  return message('Set up Telegram', [
    ['1. Browser · Get your Telegram API credentials', [
      'Sign in with your phone number and Telegram confirmation code. Open or create an API application and copy App api_id and App api_hash.',
      { label: 'Open Telegram API development tools', url: 'https://my.telegram.org/apps' },
    ]],
    ['2. VPS terminal · Prepare and activate the bridge', [
      'Reuse a private telegram-api.json or create one outside the checkout. In the private editor, enter a numeric api_id and a quoted api_hash.',
      { code: '(umask 077; touch "$HOME/telegram-api.json")\nchmod 600 "$HOME/telegram-api.json"\nnano "$HOME/telegram-api.json"' },
      { code: '{"api_id": 1234567, "api_hash": "REPLACE_WITH_YOUR_API_HASH"}' },
      { code: `${command} telegram prepare "$HOME/telegram-api.json"\n${command} telegram activate` },
      'Enter your existing Matrix operator password if prompted to link the bridge chat.',
    ]],
    ['3. Element and Telegram mobile · Sign in to Telegram', [
      'Open Telegram → Telegram Bridge and send !tg login qr. On your phone, open Telegram → Settings → Devices → Link Desktop Device and scan the QR code.',
    ]],
    ['4. Element and VPS terminal · Verify a contact or group', [
      'In Telegram Bridge, send !tg list-logins. For a contact, send !tg resolve-identifier <login-id> <telegram-username>. Verify the name and peer ID. For a group, confirm its membership and peer ID.',
      'Allow only the verified peer. Use user: for a contact, chat: for a basic group, or channel: for a supergroup without topics.',
      { code: `${command} telegram allow '<peer-type>:<peer-id>' '<login-id>'` },
      'Ask the contact or group to send a test message. Open the resulting room in Element and confirm which Telegram conversation receives your reply.',
    ]],
    ['5. VPS terminal and Element · Bind the chat and approve a draft', [
      { code: ['prepare-agent', 'sign-in', 'probe', 'start-agent'].map(step => `${command} telegram ${step}`).join('\n') },
      'Complete the Codex device sign-in in your browser. In Element, invite the local @telegram-agent account to the verified chat and send !tg set-relay <login-id>.',
      'Find the Internal Room ID in the verified chat under Room settings → Advanced. Save this example as a private JSON file. Use a unique id, your verified peer and room, and the same connected account ID for login and receiver. For a group, change trigger to request.',
      { code: JSON.stringify({ id: 'pilot-dm', peer: 'user:1001', login: '2002', receiver: '2002',
        room: '!replace-with-verified-room:matrix.example', mode: 'draft', trigger: 'inbound' }, null, 2) },
      { code: `${command} telegram bind /path/to/private-binding.json` },
      'Ask for a fresh test message in Telegram. For a group, request a draft in Telegram Control with telegram draft <binding-id> <source-event-id> using the latest verified message. Review the destination and text, select Approve and send, and confirm one delivery in Telegram.',
    ]],
  ]);
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

async function reconcile({ recipientKey, getPinned, send, setPinned, save }, groups, state) {
  const recipients = await recipientKey();
  if (!recipients) return false;
  const managed = [];
  for (const { key, entries, content } of groups) {
    const previous = [...new Set(entries.map(([name]) => state.entries[name]?.event).filter(Boolean))];
    let [event] = previous;
    if (!content) {
      if (previous.length) {
        state.replaced.push(...previous);
        for (const [name] of entries) delete state.entries[name];
        await save(state);
      }
      continue;
    }
    const revision = createHash('sha256').update(JSON.stringify(content)).digest('hex');
    if (previous.length !== 1 || entries.some(([name, body]) => {
      const entry = state.entries[name];
      return entry?.body !== body || entry.recipients !== recipients || entry.revision !== revision;
    })) {
      const transaction = createHash('sha256')
        .update(JSON.stringify([key, previous, revision, recipients])).digest('hex');
      event = await send(transaction, content);
      state.replaced.push(...previous);
      for (const [name, body] of entries) state.entries[name] = { body, event, recipients, revision };
      await save(state);
    }
    managed.push(event);
  }
  const pinned = await getPinned();
  const desired = [...new Set([...pinned.filter(id => !state.replaced.includes(id)), ...managed])];
  if (JSON.stringify(pinned) !== JSON.stringify(desired)) await setPinned(desired);
  if (state.replaced.length) {
    state.replaced = [];
    await save(state);
  }
  return true;
}

export function reconcilePins(matrix, links, state, options = {}) {
  const dashboard = shortcut(labels.dashboard, links.dashboard);
  const context = shortcut(labels.context, links.context);
  const graphSteps = [context, 'Sign in as neo4j with your dedicated Context Intelligence password, then select Connect.'];
  if (options.graphPassword) graphSteps.push('Retrieve the stored password privately on the VPS if needed.',
    { code: options.graphPassword });
  const shortcuts = message('Workspace shortcuts', [
    ['📊 Dashboard', [dashboard, 'Check VPS resource history, agent readiness, and integration status.']],
    ['🧠 Context Intelligence · Neo4j Browser', graphSteps],
  ]);
  return reconcile(matrix, [
    { key: 'workspace-shortcuts', content: shortcuts, entries: Object.entries({ dashboard, context })
      .map(([key, { label, url }]) => [key, `${label} — ${url}`]) },
    { key: 'dot-setup', content: options.dot ? dotGuide(options) : null, entries: [['dot-setup', 'Connect Dot']] },
  ], state);
}

export function reconcileTelegramPin(matrix, state, command) {
  return reconcile(matrix, [{ key: 'telegram-setup', content: telegramGuide(command),
    entries: [['telegram-setup', 'Set up Telegram']] }], state);
}
