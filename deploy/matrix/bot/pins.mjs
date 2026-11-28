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

function telegramShortcut(value) {
  if (typeof value !== 'string') return null;
  try {
    const url = new URL(value);
    if (url.origin !== 'https://matrix.to' || url.username || url.password ||
        url.pathname !== '/' || url.search || !url.hash.startsWith('#/')) return null;
    const room = decodeURIComponent(url.hash.slice(2));
    if (!/^![^\s/#?]{1,240}$/u.test(room)) return null;
    return { label: 'Open Telegram Bridge', url: url.href };
  } catch {
    return null;
  }
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

export function officeSetupNotice(command) {
  return { ...message('Finish setting up Office', [
    ['Installer still open', [
      'Continue in the same VPS terminal. If you are still on the Element sign-in page, press Enter, choose your agents, and select Done.',
      'The installer opens Amplifier provider setup for each selected local agent.',
    ]],
    ['Installer closed or interrupted', [
      'Run the appropriate command from the Constellation checkout on the VPS.',
      'Start here to resume setup from the first unfinished step.',
      { code: command },
      'If your agents are already registered, go straight to Amplifier provider setup.',
      { code: command.replace(/ setup-guide$/u, ' agent configure') },
    ]],
  ]), msgtype: 'm.notice' };
}

function dotGuide({ command, systemd, dotTunnelSupported = systemd }) {
  return message('Connect Dot', [
    ['1. Browser · Create the tunnel', [
      'Create a Secure MCP Tunnel with Tunnels Read + Manage. Associate it with the intended ChatGPT workspace and copy its tunnel ID.',
      { label: 'Open OpenAI Platform Tunnels', url: 'https://platform.openai.com/settings/organization/tunnels' },
    ]],
    ['2. Browser · Create the runtime key', [
      'Create a Restricted API key with Tunnels Read + Use.',
      { label: 'Open OpenAI API Keys', url: 'https://platform.openai.com/settings/organization/api-keys' },
    ]],
    ['3. VPS terminal · Connect Dot', dotTunnelSupported ? [
      'Replace the placeholder with your tunnel ID and enter the runtime key at the hidden prompt.',
      { code: `${command} dot-connect tunnel_REPLACE_WITH_YOUR_ID` },
    ] : ['Use a deployment with the Docker control runtime or systemd to start the tunnel.']],
    ['4. Browser · Add Constellation Office', [
      'Add a private custom MCP server named Constellation Office. Choose Tunnel, select the same tunnel ID, and choose No authentication.',
      { label: 'Open ChatGPT Plugins', url: 'https://chatgpt.com/plugins' },
      'Ask Dot to run list_agents and compare the returned agents with your Office roster.',
    ]],
  ]);
}

function telegramGuide(command, legacy, administration = false) {
  const operatorCommand = command.replace(/^sudo /u, '');
  return message('Set up Telegram', [
    ['Connect or reconnect your Telegram account', [
      'From the Constellation checkout in the VPS terminal, run the command below or continue in the open installer.',
      { code: `${operatorCommand} telegram setup` },
      'In Telegram mobile, open Settings → Devices → Link Desktop Device and scan the QR code shown in the terminal.',
      'For two-step verification, enter your Telegram password only at the hidden terminal prompt. Keep passwords and verification codes out of Element.',
    ]],
    ['Work with Telegram from Office', [
      'In Agents → Office, choose @Task Manager from the mention picker and add one request. Telegram Agent replies in that thread. You can also mention Telegram Agent here.',
      'Telegram: open the chat with @sample_contact.',
      'Telegram: create a group called Project Room with @sample_contact.',
      'Telegram: add @sample_contact to the group Project Room.',
      'Telegram: send a message to Project Room saying «The meeting starts at 10».',
      'Telegram: summarize what I missed today in Project Room.',
      'Use “prepare a draft” for a preview. An explicit send goes to the verified chat when the match is clear. Contacts and groups appear under Telegram → People.',
    ]],
    ...(administration ? [['Manage Telegram Bridge', [
      'Mention @Telegram Agent in Control Room and send one request.',
      'Telegram status · Telegram: resync groups · Telegram: restart bridge',
      'Telegram: revoke access to Project Room · Telegram: disconnect account',
      'Vote in Element to approve or decline a change. The Architect reviews bridge restarts.',
    ]]] : []),
    ...(legacy ? [['Earlier requests', [{ label: 'Open the archived Telegram Agent history',
      url: 'https://matrix.to/#/' + encodeURIComponent(legacy) }]]] : []),
  ]);
}

function recipientKeys(owner, devices) {
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
  return recipients;
}

const recipientHash = keys => createHash('sha256').update(JSON.stringify(keys)).digest('hex');

export function shortcutRecipientKey(owner, devices) {
  const keys = recipientKeys(owner, devices);
  return keys.length ? recipientHash(keys) : null;
}

export function shortcutRecipientDevices(owner, devices) {
  return recipientKeys(owner, devices).map(recipientHash).sort();
}

async function reconcile({ recipientKey, getPinned, send, setPinned, save }, groups, state, { refreshDevices }) {
  const recipients = await recipientKey();
  if (!recipients || !recipients.length) return false;
  const devices = Array.isArray(recipients) ? recipients : null;
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
      if (entry?.body !== body || entry.revision !== revision) return true;
      if (!refreshDevices) return false;
      return devices ? devices.some(device => !entry.devices?.includes(device)) : entry.recipients !== recipients;
    })) {
      const transaction = createHash('sha256')
        .update(JSON.stringify([key, previous, revision, ...(refreshDevices ? [recipients] : [])])).digest('hex');
      event = await send(transaction, content);
      state.replaced.push(...previous);
      for (const [name, body] of entries) {
        const prior = state.entries[name];
        state.entries[name] = devices ? {
          body, event, revision, devices: [...new Set([...(prior?.revision === revision ? prior.devices ?? [] : []), ...devices])].sort(),
        } : { body, event, recipients, revision };
      }
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
  const telegram = links.telegramInControlRoom ? null : telegramShortcut(links.telegramBridge);
  const archive = telegramShortcut(links.telegramArchive)?.url;
  const graphSteps = [context, 'Sign in as neo4j with your dedicated Context Intelligence password, then select Connect.'];
  if (options.graphPassword) graphSteps.push('Retrieve the stored password privately on the VPS if needed.',
    { code: options.graphPassword });
  const shortcuts = message('Workspace shortcuts', [
    ['📊 Dashboard', [dashboard, 'Check VPS resource history, agent readiness, and integration status.']],
    ['🧠 Context Intelligence · Neo4j Browser', graphSteps],
    ...(telegram ? [['✈️ Telegram', [telegram]]] : []),
  ]);
  return reconcile(matrix, [
    { key: 'workspace-shortcuts', content: shortcuts, entries: Object.entries({ dashboard, context })
      .map(([key, { label, url }]) => [key, `${label} — ${url}`]) },
    { key: 'dot-setup', content: options.dot ? dotGuide(options) : null, entries: [['dot-setup', 'Connect Dot']] },
    { key: 'telegram-setup',
      content: links.telegramInControlRoom ? telegramGuide(options.command, archive && decodeURIComponent(new URL(archive).hash.slice(2)), true) : null,
      entries: [['telegram-setup', 'Set up Telegram']] },
  ], state, { refreshDevices: false });
}

export function reconcileTelegramPin(matrix, state, command, legacy) {
  return reconcile(matrix, [{ key: 'telegram-setup', content: telegramGuide(command, legacy),
    entries: [['telegram-setup', 'Set up Telegram']] }], state, { refreshDevices: true });
}
