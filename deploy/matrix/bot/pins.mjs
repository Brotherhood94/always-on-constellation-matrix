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

function telegramGuide(command, legacy) {
  const operatorCommand = command.replace(/^sudo /u, '');
  return message('Set up Telegram', [
    ['1. Browser · Get your Telegram API credentials', [
      'Sign in with your phone number and Telegram confirmation code. Open or create an API application and copy App api_id and App api_hash.',
      { label: 'Open Telegram API development tools', url: 'https://my.telegram.org/apps' },
    ]],
    ['2. VPS terminal · Open the Telegram setup wizard', [
      'Reuse a private telegram-api.json or create one outside the checkout. In the private editor, enter a numeric api_id and a quoted api_hash.',
      { code: '(umask 077; touch "$HOME/telegram-api.json")\nchmod 600 "$HOME/telegram-api.json"\nnano "$HOME/telegram-api.json"' },
      { code: '{"api_id": 1234567, "api_hash": "REPLACE_WITH_YOUR_API_HASH"}' },
      'Select Telegram Agent in the installer to configure its provider with the other agents. If you closed the installer, resume from the VPS checkout.',
      { code: `${operatorCommand} telegram setup` },
      'Use your existing Matrix operator password if the wizard prompts you while activating the bridge.',
    ]],
    ['3. Element and Telegram mobile · Link your Telegram account', [
      'Copy the line below, paste it into the message box in Telegram Bridge, and press Enter to send it.',
      { code: '!tg login qr' },
      'Telegram Bridge replies in that chat with a QR code. On your phone, open Telegram → Settings → Devices → Link Desktop Device and scan the code.',
      'If the bot asks for your Telegram two-step verification password, send a new message in this room. Keep the !tg prefix and replace the placeholder, including the brackets.',
      { code: '!tg <your Telegram two-step verification password>' },
      'If the login expires, send !tg cancel and then !tg login qr for a fresh code.',
      'After the bot confirms sign-in, return to the VPS terminal and press Enter. This finishes linking the bridge and starts Telegram Agent if you selected it.',
      { label: 'Finish Telegram Agent setup', url: 'https://github.com/Brotherhood94/always-on-constellation-matrix/blob/main/docs/telegram.md#start-the-drafting-agent' },
    ]],
    ['4. Element · Ask Telegram Agent', [
      'Once the agent is configured, open this room, type @, and select Telegram Agent from Element’s mention picker. Adapt one example and send it.',
      'You can also mention Telegram Agent in Agents → Office. Open the private thread linked in its reply to review the result or approve a draft.',
      'To hand off through Office, mention Task Manager and say “Telegram: Send a private message to Sam Example saying «Hello».” You can also ask Dot to submit that request to Task Manager. Review the approval poll here in Telegram Bridge.',
      '@Telegram Agent, add Sam Example to my Telegram rooms.',
      '@Telegram Agent, open the private chat with @sample_contact.',
      '@Telegram Agent, add the Telegram user with ID 1001 to my rooms.',
      '@Telegram Agent, add the Project Room group to my Telegram rooms.',
      '@Telegram Agent, prepare a private message to Sam Example saying tomorrow works for me.',
      '@Telegram Agent, prepare a message for Project Room saying the meeting starts at 10.',
      '@Telegram Agent, summarize what I missed today in Project Room.',
      'Find contacts and groups under Telegram → People. Enable reading and drafting for each chat you want the agent to use.',
      'Review the verified recipient and exact text here. Only your ✅ Accept or ❌ Decline vote closes the approval poll.',
    ]],
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
  ], state, { refreshDevices: false });
}

export function reconcileTelegramPin(matrix, state, command, legacy) {
  return reconcile(matrix, [{ key: 'telegram-setup', content: telegramGuide(command, legacy),
    entries: [['telegram-setup', 'Set up Telegram']] }], state, { refreshDevices: true });
}
