import { connectionRoute, renderConnectionDetails, renderConnections, statusTone } from './connections.mjs';

const resources = { cpu: 'CPU', memory: 'Memory', disk: 'Disk' };

export function httpsLink(value) {
  if (typeof value !== 'string') throw new TypeError('Expected an HTTPS link');
  const url = new URL(value);
  if (url.protocol !== 'https:' || url.username || url.password || url.hash) {
    throw new TypeError('Use an HTTPS URL without credentials, parameters, or fragments');
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
  return url.href;
}

export function freshness(timestamp, now = Date.now()) {
  if (!Number.isFinite(timestamp) || timestamp <= 0) return 'Unavailable';
  const label = now / 1000 - timestamp > 180 ? 'Stale since' : 'Updated';
  return `${label} ${new Date(timestamp * 1000).toLocaleString()}`;
}

export function officeCatalog(catalog, integrations, telegram, selected) {
  const services = integrations?.services ?? {};
  const connector = telegram?.services?.connector;
  const lastCheck = Date.parse(telegram?.generatedAt) / 1000;
  const monitored = services['Telegram Agent'] ?? services['Telegram Control'];
  const fromTelegram = connector && Number.isFinite(lastCheck) &&
    (lastCheck > integrations?.time || (!monitored && !Number.isFinite(integrations?.time)));
  const enabled = fromTelegram || ['Telegram', 'Telegram guide', 'Telegram Agent', 'Telegram Control']
    .some(name => Object.hasOwn(services, name));
  if (!(selected ?? enabled) || !catalog?.agents || catalog.agents.some(agent => agent.key === 'telegram-agent')) return catalog;
  const manager = ['task-manager', 'manager'].find(key => catalog.agents.some(agent => agent.key === key));
  const agents = catalog.agents.map(agent => agent.key === manager ? {
    ...agent, contacts: [...new Set([...(agent.contacts ?? []), 'telegram-agent'])],
  } : agent);
  const state = fromTelegram ? (connector.health || connector.state) : monitored;
  const engine = fromTelegram ? telegram.services['telegram-engine']?.health : services['Telegram engine'];
  const status = state === 'healthy' || (state === 'running' && engine === 'healthy') ? 'Available'
    : state === 'running' && engine === 'unhealthy' ? 'Unavailable'
      : state ? state[0].toUpperCase() + state.slice(1) : services.Telegram === 'configured' ? 'Unavailable' : 'Setup required';
  const checked = fromTelegram ? Math.floor(lastCheck) : integrations?.time;
  agents.push({ key: 'telegram-agent', name: 'Telegram Agent', role: 'worker', contacts: [], placement: 'local',
    server: catalog.agents.find(agent => agent.placement === 'local')?.server ?? 'VPS', status,
    model: telegram?.agent?.model, effort: telegram?.agent?.effort,
    ...(state && Number.isSafeInteger(checked) ? { healthCheckedAt: checked } : {}),
    limits: 'Operates on verified Telegram chats through the scoped bridge.',
    persona: {
      purpose: 'Manage the Telegram Space and take Telegram assignments in Office.',
      duties: ['Find and link Telegram chats', 'Create groups and manage participants', 'Send requested messages and summarize verified chats',
        'Report results in the original Office thread'],
      working_style: 'Resolve clear recipients and ask about ambiguous matches in the original thread.',
    },
    tools: ['Scoped Telegram MCP'],
    ...(catalog.telegramPlacement ?? {}),
  });
  return { ...catalog, agents };
}

export function historyWithin(history, resource, days, now = Date.now()) {
  const collectorNames = { cpu: 'CPU', memory: 'RAM', disk: 'Disk' };
  const rows = history?.[resource] ?? history?.[collectorNames[resource]];
  return (Array.isArray(rows) ? rows : []).filter(row =>
    Array.isArray(row) && Number.isFinite(row[0]) && Number.isFinite(row[1]) &&
    row[0] >= now / 1000 - days * 86400 && row[0] <= now / 1000 + 60,
  ).sort((a, b) => a[0] - b[0]);
}

function draw(canvas, samples, days, now) {
  const style = getComputedStyle(canvas);
  const width = canvas.clientWidth || 380;
  const height = canvas.clientHeight || 120;
  const scale = Math.min(2, window.devicePixelRatio || 1);
  canvas.width = width * scale;
  canvas.height = height * scale;
  const context = canvas.getContext('2d');
  context.scale(scale, scale);
  const color = style.getPropertyValue('--chart').trim();
  const left = 35;
  const right = width - 6;
  const top = 10;
  const bottom = height - 24;
  const span = days * 86400;
  const start = now / 1000 - span;
  const range = Math.max(100, ...samples.map(row => row[1]));
  const yFor = value => bottom - Math.max(0, value) / range * (bottom - top);
  context.font = '11px system-ui';
  for (const value of [0, range / 2, range]) {
    const y = yFor(value);
    context.fillStyle = style.getPropertyValue('--muted');
    context.fillText(`${Math.round(value)}%`, 0, y + 4);
    context.strokeStyle = style.getPropertyValue('--border');
    context.beginPath();
    context.moveTo(left, y);
    context.lineTo(right, y);
    context.stroke();
  }
  const points = samples.map(([time, value]) => [
    left + Math.min(1, (time - start) / span) * (right - left), yFor(value),
  ]);
  const [firstX] = points[0];
  const [lastX, lastY] = points.at(-1);
  const trace = path => points.slice(1).forEach(([x, y]) => path.lineTo(x, y));
  const fill = context.createLinearGradient(0, top, 0, bottom);
  fill.addColorStop(0, color + '45');
  fill.addColorStop(1, color + '05');
  const area = new Path2D();
  area.moveTo(...points[0]);
  trace(area);
  area.lineTo(lastX, bottom);
  area.lineTo(firstX, bottom);
  area.closePath();
  context.fillStyle = fill;
  context.fill(area);
  context.strokeStyle = color;
  context.lineWidth = 2;
  context.beginPath();
  context.moveTo(...points[0]);
  trace(context);
  context.stroke();
  context.beginPath();
  context.arc(lastX, lastY, 3, 0, 2 * Math.PI);
  context.fillStyle = color;
  context.fill();
  context.fillStyle = style.getPropertyValue('--muted');
  context.fillText(days === 1 ? '24h ago' : `${days} days ago`, left, height - 4);
  context.textAlign = 'right';
  context.fillText('Now', right, height - 4);
}

function profileLink(agent, title) {
  const link = document.createElement('a');
  link.href = '#/agents/' + encodeURIComponent(agent.key);
  link.textContent = title;
  link.setAttribute('aria-label', title === 'Persona and duties' ? title + ' for ' + agent.name : title);
  return link;
}

function showPersona(agents) {
  const view = document.getElementById('persona-view');
  const key = /^#\/agents\/([a-z0-9_-]+)$/u.exec(location.hash)?.[1];
  view.hidden = !key;
  if (!key) return;
  const back = document.createElement('a');
  back.href = '#agents-heading';
  back.textContent = '← Agent roster';
  view.replaceChildren(back);
  const write = (tag, value) => {
    const node = document.createElement(tag);
    node.textContent = value;
    view.append(node);
    return node;
  };
  const agent = agents.find(item => item.key === key);
  if (!agent) { write('h2', 'Agent profile unavailable'); return; }
  write('h2', agent.name);
  write('p', agent.persona?.purpose || agent.limits || 'Profile awaiting registration.');
  write('h3', 'Duties');
  const duties = write('ul', '');
  for (const value of agent.persona?.duties ?? agent.capabilities?.map(item => item.description) ?? []) {
    const li = document.createElement('li');
    li.textContent = value;
    duties.append(li);
  }
  for (const [label, value] of [
    ['Working style', agent.persona?.working_style], ['Limits', agent.limits],
    ['Enabled tools', agent.tools?.join(', ') || 'None registered'],
    ['Applied persona revision', agent.revision ?? 'Starter description'],
  ]) { write('h3', label); write('p', value ?? 'Unavailable'); }
  write('h3', 'Host and readiness');
  write('p', [agent.server, agent.status].filter(Boolean).join(' · '));
  if (agent.healthCheckedAt) write('p', 'Last health check ' + new Date(agent.healthCheckedAt * 1000).toLocaleString());
  if (agent.move) write('p', agent.move.phase.replaceAll('-', ' ') + ' · ' + agent.move.from + ' → ' + agent.move.to);
  if (agent.moveHistory?.length) {
    write('h3', 'Move history');
    const moves = write('ul', '');
    for (const item of agent.moveHistory) {
      const li = document.createElement('li');
      li.textContent = new Date(item.at * 1000).toLocaleString() + ' · ' + item.from + ' → ' + item.to +
        ' · ' + item.outcome.replaceAll('-', ' ');
      moves.append(li);
    }
  }
  write('h3', 'Can contact');
  const contacts = (agent.contacts ?? []).map(id => agents.find(item => item.key === id)).filter(Boolean);
  if (!contacts.length) write('p', 'No registered contacts');
  else {
    const list = write('ul', '');
    for (const contact of contacts) {
      const li = document.createElement('li');
      li.append(profileLink(contact, contact.name));
      list.append(li);
    }
  }
}

function showView(metrics, catalog, connections) {
  const route = connectionRoute(location.hash);
  const details = document.getElementById('connection-details');
  details.hidden = route === null;
  document.getElementById('overview').hidden = Boolean(route || /^#\/agents\/[a-z0-9_-]+$/u.test(location.hash));
  showPersona(catalog?.agents ?? metrics?.agents ?? []);
  if (route) renderConnectionDetails(details, route, connections, catalog, freshness);
}

function render(metrics, roster, links, integrationStates, connections, telegram) {
  const catalog = officeCatalog(roster, integrationStates, telegram, links?.telegramAgent);
  const now = Date.now();
  const el = id => document.getElementById(id);
  const make = (tag, text, className) => Object.assign(document.createElement(tag), { textContent: text, className });
  const statusLabel = text => {
    const badge = make('span', text, 'status');
    badge.dataset.tone = statusTone(text);
    return badge;
  };
  el('freshness').textContent = freshness(metrics?.time, now);
  el('agent-freshness').textContent = freshness(catalog?.time ?? metrics?.time, now);
  try {
    el('context-link').href = httpsLink(links?.context);
    el('context-link').hidden = false;
  } catch {
    el('context-link').removeAttribute('href');
    el('context-link').hidden = true;
  }
  el('resources').replaceChildren();
  const days = Number(el('range').value);
  const charts = [];
  for (const [id, label] of Object.entries(resources)) {
    const card = make('article', '', 'card');
    card.dataset.resource = id;
    const samples = historyWithin(metrics?.history, id, days, now);
    const latest = samples.at(-1);
    card.append(make('h3', label, ''), make('p', latest ? `${latest[1].toFixed(1)}%` : '—', 'value'));
    if (latest) {
      const chart = document.createElement('canvas');
      chart.setAttribute('role', 'img');
      const values = samples.map(row => row[1]);
      chart.setAttribute('aria-label',
        `${label} usage over ${days} days. Minimum ${Math.min(...values).toFixed(1)}%, maximum ${Math.max(...values).toFixed(1)}%.`);
      card.append(chart);
      el('resources').append(card);
      charts.push([chart, samples]);
    } else {
      card.append(make('p', 'No samples in this period', ''));
      el('resources').append(card);
    }
  }
  for (const [chart, samples] of charts) draw(chart, samples, days, now);
  el('agents').replaceChildren();
  const agents = catalog?.agents ?? metrics?.agents ?? [];
  for (const agent of agents) {
    const row = document.createElement('tr');
    const status = make('td', '', '');
    status.append(statusLabel(agent.status || 'Unavailable'));
    if (agent.healthCheckedAt) status.append(make('div', `Checked ${new Date(agent.healthCheckedAt * 1000).toLocaleTimeString()}`, 'agent-detail'));
    if (agent.move) status.append(make('div', 'Destination ' + agent.move.to, 'agent-detail'));
    row.append(make('td', agent.name || '—', 'agent-name'), status, make('td', agent.server || '—', 'agent-detail'),
      make('td', [agent.model, agent.effort].filter(Boolean).join(' · ') || '—', 'agent-detail'));
    const profile = make('td', '', '');
    if (agent.key) profile.append(profileLink(agent, 'Persona and duties'));
    row.append(profile);
    el('agents').append(row);
  }
  if (!agents.length) {
    const row = document.createElement('tr');
    const cell = make('td', 'No agent inventory available', '');
    cell.colSpan = 5;
    row.append(cell);
    el('agents').append(row);
  }
  renderConnections(catalog);
  showView(metrics, catalog, connections);
  const entries = Object.entries(integrationStates?.services ?? {});
  el('integration-freshness').textContent = freshness(integrationStates?.time, now);
  el('integrations').replaceChildren();
  for (const [name, state] of entries) {
    const row = document.createElement('div');
    const value = make('dd', '', '');
    value.append(statusLabel(state));
    row.append(make('dt', name, ''), value);
    el('integrations').append(row);
  }
  const backup = document.createElement('div');
  backup.append(make('dt', 'Last successful backup', ''), make('dd', metrics?.lastBackup ?? 'Unavailable', ''));
  el('integrations').append(backup);
}

async function fetchJson(path) {
  const response = await fetch(path, { cache: 'no-store', signal: AbortSignal.timeout(5000) });
  if (!response.ok) throw new Error('Dashboard data unavailable');
  return response.json();
}

function renderTimings(snapshot) {
  const body = document.getElementById('timings');
  if (!body) return;
  const range = document.getElementById('timings-range').value;
  const scope = document.getElementById('timings-scope').value;
  const rows = (snapshot?.ranges?.[range] ?? []).filter(row => scope === 'twin' ? /^[a-f0-9]{32}$/u.test(row.scope) : row.scope === scope);
  const format = ms => Number.isFinite(ms) ? `${(ms / 1000).toFixed(1)} s` : '—';
  document.getElementById('timings-freshness').textContent = snapshot ? freshness(snapshot.time, Date.now()) + (rows.length ? '' : ' · No timing records in this period.') :
    'Timing data unavailable.';
  body.replaceChildren(...rows.map(row => {
    const el = document.createElement('tr');
    for (const value of [scope === 'twin' ? `${row.kind} · ${row.scope.slice(0, 8)}` : row.kind, row.actor, row.outcome, `${row.measured}/${row.count}`, format(row.median), format(row.p95),
      format(row.work), format(row.queue), format(row.approval)]) el.append(make('td', String(value ?? '—'), ''));
    return el;
  }));
}

if (typeof document !== 'undefined') {
  let state = [];
  async function refresh() {
    const reports = await Promise.allSettled(['metrics', 'agents', 'links', 'integrations', 'connections', 'telegram', 'maintenance', 'operation-timings'].map(path => fetchJson(path + '.json')));
    state = reports.map(({ status, value }, index) => status === 'fulfilled' ? value : state[index]);
    render(...state);
    renderTimings(state[7]);
    const release = document.getElementById('maintenance-revision');
    const revision = state[6]?.revision;
    if (release && /^[a-f0-9]{40,64}$/u.test(revision ?? '')) {
      release.hidden = false;
      release.textContent = 'Active revision ' + revision.slice(0, 12);
      release.title = revision;
    }
  }

  for (const key of ['timings-range','timings-scope']) {
    document.getElementById(key)?.addEventListener('change', () => renderTimings(state[7]));
  }
  document.getElementById('range').addEventListener('change', () => render(...state));
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => render(...state));
  window.addEventListener('hashchange', () => {
    showView(state[0], officeCatalog(state[1], state[3], state[5], state[2]?.telegramAgent), state[4]);
    const heading = document.querySelector('#connection-details:not([hidden]) h2, #persona-view:not([hidden]) h2');
    if (heading) {
      heading.tabIndex = -1;
      heading.focus();
    } else if (location.hash === '#connections-heading') {
      document.getElementById('connections-heading').scrollIntoView();
    }
  });
  void refresh();
  setInterval(refresh, 30_000);
}
